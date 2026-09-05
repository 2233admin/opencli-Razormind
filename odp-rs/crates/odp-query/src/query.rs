use std::{collections::HashMap, sync::Arc};

use axum::http::HeaderMap;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use chrono::{DateTime, Duration, Utc};
use hmac::{Hmac, Mac};
use sha2::Sha256;
use sqlx::{pool::PoolConnection, PgPool, Postgres, Row};
use tokio::sync::Mutex;
use subtle::ConstantTimeEq;

use crate::types::{
    page_size, validate_keys, Delegation, OdpQueryRequest, OdpQueryResponse, QueryMode,
    QueryModeName, ReconciliationClassification, ReconciliationResult, RecordKey, RequestError,
    RetentionState, SanitizedRecordRef, MAX_CURSOR_LENGTH, MAX_RESPONSE_BYTES,
    REDACTION_PROFILE_VERSION,
};

type HmacSha256 = Hmac<Sha256>;
const SNAPSHOT_TTL_SECONDS: i64 = 60;
const MAX_ACTIVE_SNAPSHOTS: usize = 32;

#[derive(Clone)]
pub struct QueryService {
    pool: PgPool,
    cursor_codec: CursorCodec,
    snapshots: Arc<Mutex<HashMap<uuid::Uuid, SnapshotState>>>,
}

struct SnapshotState {
    connection: PoolConnection<Postgres>,
    query_fingerprint: String,
    expires_at: DateTime<Utc>,
}

#[derive(Clone)]
pub struct CursorCodec {
    secret: Arc<[u8]>,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
struct CursorFields {
    version: u8,
    snapshot_id: uuid::Uuid,
    query_fingerprint: String,
    as_of: DateTime<Utc>,
    last_committed_at: DateTime<Utc>,
    last_id: i64,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct CursorEnvelope {
    #[serde(flatten)]
    fields: CursorFields,
    signature: String,
}

impl CursorCodec {
    pub fn new(secret: impl AsRef<[u8]>) -> Result<Self, RequestError> {
        let secret: Arc<[u8]> = Arc::from(secret.as_ref());
        if secret.len() < 32 {
            return Err(RequestError::Unavailable);
        }
        Ok(Self { secret })
    }

    pub fn encode(
        &self,
        snapshot_id: uuid::Uuid,
        query_fingerprint: &str,
        as_of: DateTime<Utc>,
        last_committed_at: DateTime<Utc>,
        last_id: i64,
    ) -> Result<String, RequestError> {
        let fields = CursorFields {
            version: 1,
            snapshot_id,
            query_fingerprint: query_fingerprint.into(),
            as_of,
            last_committed_at,
            last_id,
        };
        let signature = self.sign(&fields)?;
        let encoded = serde_json::to_vec(&CursorEnvelope { fields, signature })
            .map_err(|_| RequestError::Invalid)?;
        Ok(URL_SAFE_NO_PAD.encode(encoded))
    }

    fn decode(
        &self,
        encoded: &str,
        query_fingerprint: &str,
    ) -> Result<CursorFields, RequestError> {
        if encoded.is_empty() || encoded.len() > MAX_CURSOR_LENGTH {
            return Err(RequestError::Invalid);
        }
        let decoded = URL_SAFE_NO_PAD
            .decode(encoded)
            .map_err(|_| RequestError::Invalid)?;
        let envelope: CursorEnvelope =
            serde_json::from_slice(&decoded).map_err(|_| RequestError::Invalid)?;
        if envelope.fields.version != 1
            || envelope.fields.query_fingerprint != query_fingerprint
            || !self.verify(&envelope.fields, &envelope.signature)?
        {
            return Err(RequestError::Invalid);
        }
        Ok(envelope.fields)
    }

    fn sign(&self, fields: &CursorFields) -> Result<String, RequestError> {
        let payload = serde_json::to_vec(fields).map_err(|_| RequestError::Invalid)?;
        let mut mac = HmacSha256::new_from_slice(&self.secret).map_err(|_| RequestError::Unavailable)?;
        mac.update(&payload);
        Ok(URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes()))
    }

    fn verify(&self, fields: &CursorFields, signature: &str) -> Result<bool, RequestError> {
        let signature = URL_SAFE_NO_PAD
            .decode(signature)
            .map_err(|_| RequestError::Invalid)?;
        let payload = serde_json::to_vec(fields).map_err(|_| RequestError::Invalid)?;
        let mut mac = HmacSha256::new_from_slice(&self.secret).map_err(|_| RequestError::Unavailable)?;
        mac.update(&payload);
        Ok(mac.verify_slice(&signature).is_ok())
    }
}

impl QueryService {
    pub fn new(pool: PgPool, cursor_secret: impl AsRef<[u8]>) -> Result<Self, RequestError> {
        Ok(Self {
            pool,
            cursor_codec: CursorCodec::new(cursor_secret)?,
            snapshots: Arc::new(Mutex::new(HashMap::new())),
        })
    }

    pub async fn execute(&self, request: OdpQueryRequest) -> Result<OdpQueryResponse, RequestError> {
        request.delegation.validate(request.query.name())?;
        let response = match request.query {
            QueryMode::Exact { keys } => self.exact(&request.delegation, keys).await?,
            QueryMode::AttemptPage { cursor, page_size } => {
                self.attempt_page(&request.delegation, cursor, page_size).await?
            }
            QueryMode::Dlq { keys } => self.dlq(&request.delegation, keys).await?,
        };
        enforce_response_limit(response)
    }

    async fn exact(
        &self,
        delegation: &Delegation,
        keys: Vec<RecordKey>,
    ) -> Result<OdpQueryResponse, RequestError> {
        validate_keys(&keys, delegation)?;
        let source_ids = keys.iter().map(|key| key.source_id).collect::<Vec<_>>();
        let event_ids = keys
            .iter()
            .map(|key| key.event_id.clone())
            .collect::<Vec<_>>();
        let rows = sqlx::query(
            r#"
            SELECT requested.source_id AS requested_source_id,
                   requested.event_id AS requested_event_id,
                   record.id AS odp_record_id,
                   record.source_id,
                   record.event_id,
                   record.committed_at,
                   record.provider,
                   record.source_ts
            FROM UNNEST($1::uuid[], $2::text[]) AS requested(source_id, event_id)
            LEFT JOIN odp_records AS record
              ON record.source_id = requested.source_id
             AND record.event_id = requested.event_id
            ORDER BY requested.source_id, requested.event_id
            "#,
        )
        .bind(source_ids)
        .bind(event_ids)
        .fetch_all(&self.pool)
        .await
        .map_err(database_error)?;

        let mut results = Vec::with_capacity(rows.len());
        for row in rows {
            let key = RecordKey {
                source_id: row
                    .try_get("requested_source_id")
                    .map_err(|_| RequestError::Unavailable)?,
                event_id: row
                    .try_get("requested_event_id")
                    .map_err(|_| RequestError::Unavailable)?,
            };
            let record = sanitized_ref(&row)?;
            results.push(reconcile_exact(key, record));
        }
        Ok(base_response(
            QueryModeName::Exact,
            &delegation.query_fingerprint,
            None,
            None,
            Vec::new(),
            results,
        ))
    }

    async fn dlq(
        &self,
        delegation: &Delegation,
        keys: Vec<RecordKey>,
    ) -> Result<OdpQueryResponse, RequestError> {
        validate_keys(&keys, delegation)?;
        let source_ids = keys.iter().map(|key| key.source_id).collect::<Vec<_>>();
        let event_ids = keys
            .iter()
            .map(|key| key.event_id.clone())
            .collect::<Vec<_>>();
        let rows = sqlx::query(
            r#"
            SELECT requested.source_id AS requested_source_id,
                   requested.event_id AS requested_event_id,
                   record.id AS odp_record_id,
                   record.source_id,
                   record.event_id,
                   record.committed_at,
                   record.provider,
                   record.source_ts,
                   EXISTS (
                       SELECT 1
                       FROM odp_dlq AS dlq
                       WHERE dlq.source_id = requested.source_id
                         AND dlq.event_id = requested.event_id
                   ) AS has_dlq
            FROM UNNEST($1::uuid[], $2::text[]) AS requested(source_id, event_id)
            LEFT JOIN odp_records AS record
              ON record.source_id = requested.source_id
             AND record.event_id = requested.event_id
            ORDER BY requested.source_id, requested.event_id
            "#,
        )
        .bind(source_ids)
        .bind(event_ids)
        .fetch_all(&self.pool)
        .await
        .map_err(database_error)?;

        let mut results = Vec::with_capacity(rows.len());
        for row in rows {
            let key = RecordKey {
                source_id: row
                    .try_get("requested_source_id")
                    .map_err(|_| RequestError::Unavailable)?,
                event_id: row
                    .try_get("requested_event_id")
                    .map_err(|_| RequestError::Unavailable)?,
            };
            let record = sanitized_ref(&row)?;
            let has_dlq: bool = row.try_get("has_dlq").map_err(|_| RequestError::Unavailable)?;
            results.push(reconcile_dlq(key, record, has_dlq));
        }
        Ok(base_response(
            QueryModeName::Dlq,
            &delegation.query_fingerprint,
            None,
            None,
            Vec::new(),
            results,
        ))
    }

    async fn attempt_page(
        &self,
        delegation: &Delegation,
        cursor: Option<String>,
        requested_page_size: Option<u16>,
    ) -> Result<OdpQueryResponse, RequestError> {
        let page_size = page_size(requested_page_size)?;
        let now = Utc::now();
        let (snapshot_id, as_of, last_committed_at, last_id, mut connection) = match cursor {
            Some(cursor) => {
                let cursor = self.cursor_codec.decode(&cursor, &delegation.query_fingerprint)?;
                let mut snapshots = self.snapshots.lock().await;
                snapshots.retain(|_, snapshot| snapshot.expires_at > now);
                let snapshot = snapshots
                    .remove(&cursor.snapshot_id)
                    .ok_or(RequestError::Unavailable)?;
                if snapshot.query_fingerprint != delegation.query_fingerprint
                    || snapshot.expires_at <= now
                {
                    return Err(RequestError::Unavailable);
                }
                (
                    cursor.snapshot_id,
                    cursor.as_of,
                    Some(cursor.last_committed_at),
                    Some(cursor.last_id),
                    snapshot.connection,
                )
            }
            None => {
                let mut snapshots = self.snapshots.lock().await;
                snapshots.retain(|_, snapshot| snapshot.expires_at > now);
                if snapshots.len() >= MAX_ACTIVE_SNAPSHOTS {
                    return Err(RequestError::Unavailable);
                }
                drop(snapshots);
                let mut connection = self.pool.acquire().await.map_err(database_error)?;
                sqlx::query("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    .execute(&mut *connection)
                    .await
                    .map_err(database_error)?;
                let as_of = sqlx::query_scalar("SELECT NOW()")
                    .fetch_one(&mut *connection)
                    .await
                    .map_err(database_error)?;
                (uuid::Uuid::new_v4(), as_of, None, None, connection)
            }
        };
        let mut rows = sqlx::query(
            r#"
            SELECT id, source_id, event_id, committed_at, provider, source_ts
            FROM odp_records
            WHERE task_id = $1
              AND trace_id = $2
              AND source_id = ANY($3::uuid[])
              AND committed_at <= $4
              AND ($5::timestamptz IS NULL OR (committed_at, id) > ($5, $6))
            ORDER BY committed_at ASC, id ASC
            LIMIT $7
            "#,
        )
        .bind(delegation.task_id)
        .bind(delegation.trace_id)
        .bind(&delegation.allowed_source_ids)
        .bind(as_of)
        .bind(last_committed_at)
        .bind(last_id)
        .bind(i64::from(page_size) + 1)
        .fetch_all(&mut *connection)
        .await
        .map_err(database_error)?
        .into_iter()
        .map(|row| sanitized_ref(&row)?.ok_or(RequestError::Unavailable))
        .collect::<Result<Vec<_>, _>>()?;

        let has_next_page = rows.len() > usize::from(page_size);
        if has_next_page {
            rows.pop();
        }
        let next_cursor = match rows.last() {
            Some(record) if has_next_page => {
                let expires_at = Utc::now() + Duration::seconds(SNAPSHOT_TTL_SECONDS);
                self.snapshots.lock().await.insert(
                    snapshot_id,
                    SnapshotState {
                        connection,
                        query_fingerprint: delegation.query_fingerprint.clone(),
                        expires_at,
                    },
                );
                Some(self.cursor_codec.encode(
                    snapshot_id,
                    &delegation.query_fingerprint,
                    as_of,
                    record.committed_at,
                    record.odp_record_id,
                )?)
            }
            _ => {
                sqlx::query("ROLLBACK")
                    .execute(&mut *connection)
                    .await
                    .map_err(database_error)?;
                None
            }
        };
        Ok(base_response(
            QueryModeName::AttemptPage,
            &delegation.query_fingerprint,
            Some(as_of),
            next_cursor,
            rows,
            Vec::new(),
        ))
    }
}

pub fn machine_authorized(headers: &HeaderMap, credential: &str) -> bool {
    let Some(value) = headers.get(axum::http::header::AUTHORIZATION) else {
        return false;
    };
    let Ok(value) = value.to_str() else {
        return false;
    };
    let Some(candidate) = value.strip_prefix("Bearer ") else {
        return false;
    };
    candidate.as_bytes().ct_eq(credential.as_bytes()).into()
}

fn sanitized_ref(row: &sqlx::postgres::PgRow) -> Result<Option<SanitizedRecordRef>, RequestError> {
    let odp_record_id: Option<i64> = row
        .try_get("odp_record_id")
        .or_else(|_| row.try_get("id"))
        .map_err(|_| RequestError::Unavailable)?;
    let Some(odp_record_id) = odp_record_id else {
        return Ok(None);
    };
    Ok(Some(SanitizedRecordRef {
        source_id: row.try_get("source_id").map_err(|_| RequestError::Unavailable)?,
        event_id: row.try_get("event_id").map_err(|_| RequestError::Unavailable)?,
        odp_record_id,
        committed_at: row
            .try_get("committed_at")
            .map_err(|_| RequestError::Unavailable)?,
        provider: row.try_get("provider").map_err(|_| RequestError::Unavailable)?,
        source_ts: row.try_get("source_ts").map_err(|_| RequestError::Unavailable)?,
    }))
}

fn reconcile_exact(key: RecordKey, record: Option<SanitizedRecordRef>) -> ReconciliationResult {
    ReconciliationResult {
        key,
        classification: if record.is_some() {
            ReconciliationClassification::Present
        } else {
            ReconciliationClassification::Unknown
        },
        retention_state: RetentionState::Unknown,
        record,
    }
}

fn reconcile_dlq(
    key: RecordKey,
    record: Option<SanitizedRecordRef>,
    has_dlq: bool,
) -> ReconciliationResult {
    if let Some(record) = record {
        return reconcile_exact(key, Some(record));
    }
    ReconciliationResult {
        key,
        classification: if has_dlq {
            ReconciliationClassification::Dlq
        } else {
            ReconciliationClassification::Unknown
        },
        retention_state: if has_dlq {
            RetentionState::Retained
        } else {
            RetentionState::Unknown
        },
        record: None,
    }
}

fn base_response(
    mode: QueryModeName,
    query_fingerprint: &str,
    as_of: Option<DateTime<Utc>>,
    next_cursor: Option<String>,
    records: Vec<SanitizedRecordRef>,
    results: Vec<ReconciliationResult>,
) -> OdpQueryResponse {
    let retention_state = if mode == QueryModeName::Dlq
        && !results.is_empty()
        && results.iter().all(|result| {
            result.classification == ReconciliationClassification::Dlq
                && result.retention_state == RetentionState::Retained
        })
    {
        RetentionState::Retained
    } else {
        RetentionState::Unknown
    };
    OdpQueryResponse {
        mode,
        query_fingerprint: query_fingerprint.into(),
        as_of,
        next_cursor,
        retention_state,
        redaction_profile_version: REDACTION_PROFILE_VERSION,
        records,
        results,
    }
}

fn enforce_response_limit(response: OdpQueryResponse) -> Result<OdpQueryResponse, RequestError> {
    let size = serde_json::to_vec(&response)
        .map_err(|_| RequestError::Unavailable)?
        .len();
    if size > MAX_RESPONSE_BYTES {
        return Err(RequestError::ResponseTooLarge);
    }
    Ok(response)
}

fn database_error(error: sqlx::Error) -> RequestError {
    tracing::warn!(error = %error, "odp-query database operation failed");
    RequestError::Unavailable
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{QueryModeName, REDACTION_PROFILE_VERSION};
    use chrono::{Duration, TimeZone};
    use uuid::Uuid;

    fn record() -> SanitizedRecordRef {
        SanitizedRecordRef {
            source_id: Uuid::new_v4(),
            event_id: "event".into(),
            odp_record_id: 9,
            committed_at: Utc.with_ymd_and_hms(2026, 1, 1, 0, 0, 1).unwrap(),
            provider: "rss".into(),
            source_ts: Utc.with_ymd_and_hms(2026, 1, 1, 0, 0, 0).unwrap(),
        }
    }

    #[test]
    fn exact_lookup_only_reports_presence_or_unknown() {
        let reference = record();
        let present = reconcile_exact(
            RecordKey {
                source_id: reference.source_id,
                event_id: reference.event_id.clone(),
            },
            Some(reference),
        );
        assert_eq!(present.classification, ReconciliationClassification::Present);
        assert_eq!(present.retention_state, RetentionState::Unknown);
        assert!(present.record.is_some());

        let unknown = reconcile_exact(
            RecordKey {
                source_id: Uuid::new_v4(),
                event_id: "missing".into(),
            },
            None,
        );
        assert_eq!(unknown.classification, ReconciliationClassification::Unknown);
        assert!(unknown.record.is_none());
    }

    #[test]
    fn dlq_rechecks_exact_precedence_and_reports_durable_rows() {
        let key = RecordKey {
            source_id: uuid::Uuid::nil(),
            event_id: "event".into(),
        };
        let retained = reconcile_dlq(key.clone(), None, true);
        assert_eq!(retained.classification, ReconciliationClassification::Dlq);
        assert_eq!(retained.retention_state, RetentionState::Retained);
        assert!(retained.record.is_none());

        let missing = reconcile_dlq(key.clone(), None, false);
        assert_eq!(missing.classification, ReconciliationClassification::Unknown);
        assert_eq!(missing.retention_state, RetentionState::Unknown);

        let reference = record();
        let present = reconcile_dlq(
            RecordKey {
                source_id: reference.source_id,
                event_id: reference.event_id.clone(),
            },
            Some(reference),
            true,
        );
        assert_eq!(present.classification, ReconciliationClassification::Present);
        assert_eq!(present.retention_state, RetentionState::Unknown);
        assert!(present.record.is_some());

        let all_dlq = base_response(
            QueryModeName::Dlq,
            "fingerprint",
            None,
            None,
            Vec::new(),
            vec![retained.clone()],
        );
        assert_eq!(all_dlq.retention_state, RetentionState::Retained);

        let mixed = base_response(
            QueryModeName::Dlq,
            "fingerprint",
            None,
            None,
            Vec::new(),
            vec![retained, missing],
        );
        assert_eq!(mixed.retention_state, RetentionState::Unknown);
    }

    #[test]
    fn cursor_is_signed_scope_bound_and_retains_first_snapshot() {
        let codec = CursorCodec::new("a sufficiently long cursor signing key").unwrap();
        let fingerprint = "scope-fingerprint";
        let as_of = Utc.with_ymd_and_hms(2026, 1, 2, 3, 4, 5).unwrap();
        let cursor = codec
            .encode(uuid::Uuid::new_v4(), fingerprint, as_of, as_of, 42)
            .unwrap();
        let decoded = codec.decode(&cursor, fingerprint).unwrap();
        assert_eq!(decoded.as_of, as_of);
        assert_eq!(decoded.last_id, 42);
        assert_eq!(codec.decode(&cursor, "different-scope"), Err(RequestError::Invalid));

        let mut tampered = cursor.into_bytes();
        let last = tampered.len() - 1;
        tampered[last] = if tampered[last] == b'A' { b'B' } else { b'A' };
        assert_eq!(
            codec.decode(std::str::from_utf8(&tampered).unwrap(), fingerprint),
            Err(RequestError::Invalid)
        );
    }

    #[test]
    fn only_the_configured_admin_machine_credential_is_authorized() {
        let mut headers = HeaderMap::new();
        headers.insert("authorization", "Bearer admin-machine-secret".parse().unwrap());
        assert!(machine_authorized(&headers, "admin-machine-secret"));
        assert!(!machine_authorized(&headers, "browser-credential"));
    }

    #[test]
    fn response_is_bounded_and_server_redacted() {
        let response = base_response(
            QueryModeName::AttemptPage,
            "fingerprint",
            None,
            None,
            vec![record()],
            Vec::new(),
        );
        let encoded = serde_json::to_string(&response).unwrap();
        assert!(encoded.contains(REDACTION_PROFILE_VERSION));
        assert!(!encoded.contains("payload"));
        assert!(!encoded.contains("raw_data"));
        assert_eq!(enforce_response_limit(response).unwrap().records.len(), 1);
    }

    #[test]
    fn page_mode_is_only_available_when_delegated() {
        let source_id = Uuid::new_v4();
        let mut delegation = Delegation {
            workspace_id: "workspace".into(),
            project_id: "project".into(),
            workflow_id: "workflow".into(),
            run_id: "run".into(),
            batch_id: "batch".into(),
            attempt_id: "attempt".into(),
            task_id: Uuid::new_v4(),
            trace_id: Uuid::new_v4(),
            allowed_source_ids: vec![source_id],
            query_fingerprint: String::new(),
            allowed_fields: [
                "source_id",
                "event_id",
                "odp_record_id",
                "committed_at",
                "provider",
                "source_ts",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
            allowed_modes: vec![QueryModeName::Exact],
            expires_at: Utc::now() + Duration::minutes(5),
        };
        delegation.query_fingerprint = delegation.fingerprint();
        assert_eq!(
            delegation.validate(QueryModeName::AttemptPage),
            Err(RequestError::Invalid)
        );
    }
    #[tokio::test]
    #[ignore = "requires ODP_QUERY_TEST_DATABASE_URL"]
    async fn postgres_snapshot_excludes_a_page_race() {
        let database_url = std::env::var("ODP_QUERY_TEST_DATABASE_URL").unwrap();
        let pool = PgPool::connect(&database_url).await.unwrap();
        sqlx::query(
            "CREATE TABLE IF NOT EXISTS odp_records (
                id BIGSERIAL PRIMARY KEY,
                task_id UUID NOT NULL,
                trace_id UUID NOT NULL,
                source_id UUID NOT NULL,
                event_id TEXT NOT NULL,
                committed_at TIMESTAMPTZ NOT NULL,
                provider TEXT NOT NULL,
                source_ts TIMESTAMPTZ NOT NULL
            )",
        )
        .execute(&pool)
        .await
        .unwrap();
        sqlx::query("TRUNCATE odp_records RESTART IDENTITY").execute(&pool).await.unwrap();

        let source_id = Uuid::new_v4();
        let task_id = Uuid::new_v4();
        let trace_id = Uuid::new_v4();
        let mut delegation = Delegation {
            workspace_id: "workspace".into(),
            project_id: "project".into(),
            workflow_id: "workflow".into(),
            run_id: "run".into(),
            batch_id: "batch".into(),
            attempt_id: "attempt".into(),
            task_id,
            trace_id,
            allowed_source_ids: vec![source_id],
            query_fingerprint: String::new(),
            allowed_fields: [
                "source_id", "event_id", "odp_record_id", "committed_at", "provider", "source_ts",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
            allowed_modes: vec![QueryModeName::AttemptPage],
            expires_at: Utc::now() + Duration::minutes(5),
        };
        delegation.query_fingerprint = delegation.fingerprint();
        for (event_id, offset) in [("first", 4_i64), ("third", 2_i64)] {
            sqlx::query(
                "INSERT INTO odp_records
                 (task_id, trace_id, source_id, event_id, committed_at, provider, source_ts)
                 VALUES ($1, $2, $3, $4, NOW() - ($5 * INTERVAL '1 second'), 'rss', NOW())",
            )
            .bind(task_id)
            .bind(trace_id)
            .bind(source_id)
            .bind(event_id)
            .bind(offset)
            .execute(&pool)
            .await
            .unwrap();
        }

        let service = QueryService::new(pool.clone(), "a sufficiently long cursor signing key").unwrap();
        let first_page = service.attempt_page(&delegation, None, Some(1)).await.unwrap();
        let cursor = first_page.next_cursor.unwrap();
        sqlx::query(
            "INSERT INTO odp_records
             (task_id, trace_id, source_id, event_id, committed_at, provider, source_ts)
             VALUES ($1, $2, $3, 'racing-row', $4 - INTERVAL '3 seconds', 'rss', $4)",
        )
        .bind(task_id)
        .bind(trace_id)
        .bind(source_id)
        .bind(first_page.as_of.unwrap())
        .execute(&pool)
        .await
        .unwrap();

        let second_page = service.attempt_page(&delegation, Some(cursor), Some(1)).await.unwrap();
        assert_eq!(second_page.records[0].event_id, "third");
        assert_eq!(second_page.records.len(), 1);
    }

    #[tokio::test]
    #[ignore = "requires ODP_QUERY_TEST_DATABASE_URL"]
    async fn postgres_dlq_reconciliation_reads_only_durable_exact_rows() {
        let database_url = std::env::var("ODP_QUERY_TEST_DATABASE_URL").unwrap();
        let pool = PgPool::connect(&database_url).await.unwrap();
        sqlx::query(
            "CREATE TABLE IF NOT EXISTS odp_dlq (
                id BIGSERIAL PRIMARY KEY,
                stream_id TEXT NOT NULL,
                provider TEXT,
                source_id UUID,
                event_id TEXT,
                error TEXT NOT NULL,
                delivery_count INT NOT NULL,
                payload JSONB,
                failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )",
        )
        .execute(&pool)
        .await
        .unwrap();
        sqlx::query(
            "CREATE TABLE IF NOT EXISTS odp_records (
                id BIGSERIAL PRIMARY KEY,
                task_id UUID NOT NULL,
                trace_id UUID NOT NULL,
                source_id UUID NOT NULL,
                event_id TEXT NOT NULL,
                committed_at TIMESTAMPTZ NOT NULL,
                provider TEXT NOT NULL,
                source_ts TIMESTAMPTZ NOT NULL
            )",
        )
        .execute(&pool)
        .await
        .unwrap();
        sqlx::query("TRUNCATE odp_dlq RESTART IDENTITY")
            .execute(&pool)
            .await
            .unwrap();
        sqlx::query("TRUNCATE odp_records RESTART IDENTITY")
            .execute(&pool)
            .await
            .unwrap();

        let source_id = Uuid::new_v4();
        let retained = RecordKey {
            source_id,
            event_id: "durable".into(),
        };
        let missing = RecordKey {
            source_id,
            event_id: "missing".into(),
        };
        sqlx::query(
            "INSERT INTO odp_dlq (stream_id, source_id, event_id, error, delivery_count)
             VALUES ('stream-1', $1, $2, 'poison', 5)",
        )
        .bind(source_id)
        .bind(&retained.event_id)
        .execute(&pool)
        .await
        .unwrap();

        let mut delegation = Delegation {
            workspace_id: "workspace".into(),
            project_id: "project".into(),
            workflow_id: "workflow".into(),
            run_id: "run".into(),
            batch_id: "batch".into(),
            attempt_id: "attempt".into(),
            task_id: Uuid::new_v4(),
            trace_id: Uuid::new_v4(),
            allowed_source_ids: vec![source_id],
            query_fingerprint: String::new(),
            allowed_fields: [
                "source_id", "event_id", "odp_record_id", "committed_at", "provider", "source_ts",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
            allowed_modes: vec![QueryModeName::Dlq],
            expires_at: Utc::now() + Duration::minutes(5),
        };
        delegation.query_fingerprint = delegation.fingerprint();
        let service =
            QueryService::new(pool.clone(), "a sufficiently long cursor signing key").unwrap();

        let mixed = service
            .dlq(&delegation, vec![retained.clone(), missing])
            .await
            .unwrap();
        assert_eq!(mixed.retention_state, RetentionState::Unknown);
        assert_eq!(
            mixed
                .results
                .iter()
                .find(|result| result.key.event_id == retained.event_id)
                .unwrap()
                .classification,
            ReconciliationClassification::Dlq
        );

        let only_retained = service.dlq(&delegation, vec![retained.clone()]).await.unwrap();
        assert_eq!(only_retained.retention_state, RetentionState::Retained);
        assert!(only_retained.records.is_empty());

        sqlx::query(
            "INSERT INTO odp_records
             (task_id, trace_id, source_id, event_id, committed_at, provider, source_ts)
             VALUES ($1, $2, $3, $4, NOW(), 'rss', NOW())",
        )
        .bind(delegation.task_id)
        .bind(delegation.trace_id)
        .bind(source_id)
        .bind(&retained.event_id)
        .execute(&pool)
        .await
        .unwrap();
        let present = service.dlq(&delegation, vec![retained]).await.unwrap();
        assert_eq!(
            present.results[0].classification,
            ReconciliationClassification::Present
        );
        assert!(present.results[0].record.is_some());
    }
}
