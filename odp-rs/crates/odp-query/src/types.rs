use std::collections::BTreeSet;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use uuid::Uuid;

pub const MAX_ALLOWED_SOURCES: usize = 100;
pub const MAX_KEYS: usize = 100;
pub const MAX_PAGE_SIZE: u16 = 100;
pub const DEFAULT_PAGE_SIZE: u16 = 50;
pub const MAX_CURSOR_LENGTH: usize = 4096;
pub const MAX_RESPONSE_BYTES: usize = 128 * 1024;
pub const REDACTION_PROFILE_VERSION: &str = "odp-query-reference-v1";
const SAFE_FIELDS: [&str; 6] = [
    "source_id",
    "event_id",
    "odp_record_id",
    "committed_at",
    "provider",
    "source_ts",
];

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum QueryModeName {
    Exact,
    AttemptPage,
    Dlq,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Delegation {
    pub workspace_id: String,
    pub project_id: String,
    pub workflow_id: String,
    pub run_id: String,
    pub batch_id: String,
    pub attempt_id: String,
    pub task_id: Uuid,
    pub trace_id: Uuid,
    pub allowed_source_ids: Vec<Uuid>,
    pub query_fingerprint: String,
    pub allowed_fields: Vec<String>,
    pub allowed_modes: Vec<QueryModeName>,
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(deny_unknown_fields)]
pub struct RecordKey {
    pub source_id: Uuid,
    pub event_id: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub enum QueryMode {
    Exact { keys: Vec<RecordKey> },
    AttemptPage {
        #[serde(default)]
        cursor: Option<String>,
        #[serde(default)]
        page_size: Option<u16>,
    },
    Dlq { keys: Vec<RecordKey> },
}

impl QueryMode {
    pub fn name(&self) -> QueryModeName {
        match self {
            Self::Exact { .. } => QueryModeName::Exact,
            Self::AttemptPage { .. } => QueryModeName::AttemptPage,
            Self::Dlq { .. } => QueryModeName::Dlq,
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct OdpQueryRequest {
    pub delegation: Delegation,
    #[serde(flatten)]
    pub query: QueryMode,
}

#[derive(Debug, Clone, Serialize)]
pub struct SanitizedRecordRef {
    pub source_id: Uuid,
    pub event_id: String,
    pub odp_record_id: i64,
    pub committed_at: DateTime<Utc>,
    pub provider: String,
    pub source_ts: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ReconciliationClassification {
    Present,
    Dlq,
    Unknown,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RetentionState {
    Retained,
    Unknown,
}

#[derive(Debug, Clone, Serialize)]
pub struct ReconciliationResult {
    pub key: RecordKey,
    pub classification: ReconciliationClassification,
    pub retention_state: RetentionState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub record: Option<SanitizedRecordRef>,
}

#[derive(Debug, Clone, Serialize)]
pub struct OdpQueryResponse {
    pub mode: QueryModeName,
    pub query_fingerprint: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub as_of: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_cursor: Option<String>,
    pub retention_state: RetentionState,
    pub redaction_profile_version: &'static str,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub records: Vec<SanitizedRecordRef>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub results: Vec<ReconciliationResult>,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum RequestError {
    #[error("request rejected")]
    Invalid,
    #[error("request unauthorized")]
    Unauthorized,
    #[error("query unavailable")]
    Unavailable,
    #[error("response too large")]
    ResponseTooLarge,
}

impl Delegation {
    pub fn validate(&self, mode: QueryModeName) -> Result<(), RequestError> {
        if self.expires_at <= Utc::now()
            || self.allowed_source_ids.is_empty()
            || self.allowed_source_ids.len() > MAX_ALLOWED_SOURCES
            || has_duplicates(&self.allowed_source_ids)
            || !self.allowed_modes.contains(&mode)
            || self.query_fingerprint != self.fingerprint()
            || !has_exact_safe_fields(&self.allowed_fields)
            || ![
                &self.workspace_id,
                &self.project_id,
                &self.workflow_id,
                &self.run_id,
                &self.batch_id,
                &self.attempt_id,
            ]
            .iter()
            .all(|value| !value.trim().is_empty())
        {
            return Err(RequestError::Invalid);
        }
        Ok(())
    }

    pub fn fingerprint(&self) -> String {
        #[derive(Serialize)]
        struct Fingerprint<'a> {
            workspace_id: &'a str,
            project_id: &'a str,
            workflow_id: &'a str,
            run_id: &'a str,
            batch_id: &'a str,
            attempt_id: &'a str,
            task_id: Uuid,
            trace_id: Uuid,
            allowed_source_ids: Vec<Uuid>,
            allowed_fields: Vec<&'a str>,
            allowed_modes: Vec<QueryModeName>,
        }

        let mut source_ids = self.allowed_source_ids.clone();
        source_ids.sort_unstable();
        let mut allowed_fields = self
            .allowed_fields
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>();
        allowed_fields.sort_unstable();
        let mut allowed_modes = self.allowed_modes.clone();
        allowed_modes.sort_by_key(|mode| match mode {
            QueryModeName::Exact => 0,
            QueryModeName::AttemptPage => 1,
            QueryModeName::Dlq => 2,
        });
        let encoded = serde_json::to_vec(&Fingerprint {
            workspace_id: &self.workspace_id,
            project_id: &self.project_id,
            workflow_id: &self.workflow_id,
            run_id: &self.run_id,
            batch_id: &self.batch_id,
            attempt_id: &self.attempt_id,
            task_id: self.task_id,
            trace_id: self.trace_id,
            allowed_source_ids: source_ids,
            allowed_fields,
            allowed_modes,
        })
        .expect("fingerprint input is serializable");
        hex_sha256(&encoded)
    }
}

pub fn validate_keys(keys: &[RecordKey], delegation: &Delegation) -> Result<(), RequestError> {
    if keys.is_empty() || keys.len() > MAX_KEYS || has_duplicates(keys) {
        return Err(RequestError::Invalid);
    }
    for key in keys {
        if key.event_id.is_empty()
            || key.event_id.len() > 512
            || !delegation.allowed_source_ids.contains(&key.source_id)
        {
            return Err(RequestError::Invalid);
        }
    }
    Ok(())
}

pub fn page_size(value: Option<u16>) -> Result<u16, RequestError> {
    let value = value.unwrap_or(DEFAULT_PAGE_SIZE);
    if value == 0 || value > MAX_PAGE_SIZE {
        return Err(RequestError::Invalid);
    }
    Ok(value)
}

fn has_duplicates<T: Ord + Clone>(items: &[T]) -> bool {
    let mut seen = BTreeSet::new();
    items.iter().any(|item| !seen.insert(item.clone()))
}

fn has_exact_safe_fields(fields: &[String]) -> bool {
    fields.iter().map(String::as_str).collect::<BTreeSet<_>>()
        == SAFE_FIELDS.iter().copied().collect()
        && fields.len() == SAFE_FIELDS.len()
}

fn hex_sha256(value: &[u8]) -> String {
    let digest = Sha256::digest(value);
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;

    fn delegation() -> Delegation {
        let mut delegation = Delegation {
            workspace_id: "workspace".into(),
            project_id: "project".into(),
            workflow_id: "workflow".into(),
            run_id: "run".into(),
            batch_id: "batch".into(),
            attempt_id: "attempt".into(),
            task_id: Uuid::new_v4(),
            trace_id: Uuid::new_v4(),
            allowed_source_ids: vec![Uuid::new_v4()],
            query_fingerprint: String::new(),
            allowed_fields: SAFE_FIELDS.iter().map(|field| (*field).into()).collect(),
            allowed_modes: vec![
                QueryModeName::Exact,
                QueryModeName::AttemptPage,
                QueryModeName::Dlq,
            ],
            expires_at: Utc::now() + Duration::minutes(5),
        };
        delegation.query_fingerprint = delegation.fingerprint();
        delegation
    }

    #[test]
    fn delegation_rejects_tampered_scope_and_expiry() {
        let mut value = delegation();
        assert_eq!(value.validate(QueryModeName::Exact), Ok(()));
        value.run_id = "other-run".into();
        assert_eq!(value.validate(QueryModeName::Exact), Err(RequestError::Invalid));

        let mut expired = delegation();
        expired.expires_at = Utc::now() - Duration::seconds(1);
        expired.query_fingerprint = expired.fingerprint();
        assert_eq!(expired.validate(QueryModeName::Exact), Err(RequestError::Invalid));
    }

    #[test]
    fn fingerprint_ignores_renewal_but_uses_canonical_mode_order() {
        let original = delegation();
        let mut renewed = original.clone();
        renewed.expires_at += Duration::minutes(5);
        assert_eq!(original.fingerprint(), renewed.fingerprint());

        let mut reordered = original.clone();
        reordered.allowed_modes.reverse();
        assert_eq!(original.fingerprint(), reordered.fingerprint());
    }

    #[test]
    fn unsafe_or_oversized_exact_key_sets_are_rejected() {
        let scope = delegation();
        let key = RecordKey {
            source_id: Uuid::new_v4(),
            event_id: "not-authorized".into(),
        };
        assert_eq!(validate_keys(&[key], &scope), Err(RequestError::Invalid));

        let allowed = RecordKey {
            source_id: scope.allowed_source_ids[0],
            event_id: "x".repeat(513),
        };
        assert_eq!(validate_keys(&[allowed], &scope), Err(RequestError::Invalid));
        assert_eq!(page_size(Some(MAX_PAGE_SIZE + 1)), Err(RequestError::Invalid));
    }

    #[test]
    fn request_rejects_raw_jsonb_predicates() {
        let scope = delegation();
        let value = serde_json::json!({
            "delegation": scope,
            "mode": "attempt_page",
            "raw_data": {"path": "secret"}
        });
        assert!(serde_json::from_value::<OdpQueryRequest>(value).is_err());
    }

    #[test]
    fn sanitized_reference_never_serializes_payload_fields() {
        let reference = SanitizedRecordRef {
            source_id: Uuid::new_v4(),
            event_id: "event".into(),
            odp_record_id: 7,
            committed_at: Utc::now(),
            provider: "rss".into(),
            source_ts: Utc::now(),
        };
        let encoded = serde_json::to_string(&reference).unwrap();
        assert!(!encoded.contains("payload"));
        assert!(!encoded.contains("raw_data"));
        assert!(!encoded.contains("cookie"));
    }
}
