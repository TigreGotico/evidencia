import pytest

from evidencia import Config, HttpDecision, LocalDecision


def test_file_then_environment(tmp_path):
    path = tmp_path / "evidencia.toml"
    path.write_text('top_k = 2\nfail_open = false\n[decision]\nbackend = "http"\nurl = "http://a:1"\n')
    cfg = Config.load(str(path), env={"EVIDENCIA_DECISION_URL": "http://b:2", "EVIDENCIA_API_KEY": "k"})
    assert cfg.top_k == 2 and cfg.fail_open is False
    assert cfg.decision.url == "http://b:2"
    assert cfg.api_key == "k"


def test_unknown_key_is_an_error(tmp_path):
    path = tmp_path / "evidencia.toml"
    path.write_text("topk = 2\n")
    with pytest.raises(ValueError, match="topk"):
        Config.load(str(path), env={})


def test_threshold_out_of_range():
    with pytest.raises(ValueError):
        Config(grounded_threshold=1.5)


def test_backend_selection():
    assert isinstance(Config().decision.build(), HttpDecision)
    assert Config().decision.run == "TigreGotico/jevasio-0.8b-v2"
    local = Config(decision={"backend": "local", "run": "x/y"}).decision.build()
    assert isinstance(local, LocalDecision) and local.run == "x/y"
    with pytest.raises(ValueError):
        Config(decision={"backend": "grpc"}).decision.build()


@pytest.mark.parametrize("bad", [{"grounded_threshold": "0.5"}, {"select_threshold": True}, {"top_k": 0},
                                 {"max_rounds": 1.5}, {"api_key": ""}])
def test_bad_values_are_value_errors(bad):
    with pytest.raises(ValueError):
        Config(**bad)


def test_unknown_decision_key_is_a_value_error(tmp_path):
    path = tmp_path / "evidencia.toml"
    path.write_text('[decision]\nbakend = "http"\n')
    with pytest.raises(ValueError, match="bakend"):
        Config.load(str(path), env={})
