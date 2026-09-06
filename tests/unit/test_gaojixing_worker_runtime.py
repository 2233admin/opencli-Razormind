from backend.workflow.gaojixing_worker_runtime import _build_managed_doubao_driver


def test_managed_driver_keeps_target_identity_in_durable_run_root(tmp_path):
    run_root = tmp_path / "runs" / "run-1"
    attempt_root = run_root / ".worker-staging" / "1-attempt"
    attempt_root.mkdir(parents=True)

    driver = _build_managed_doubao_driver(attempt_root)

    assert driver._project_root == attempt_root.resolve()
    assert driver._target_state_root == run_root.resolve()
