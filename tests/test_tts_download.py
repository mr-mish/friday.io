from friday.voice.tts import download_env


def test_download_env_provides_ca_bundle(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    env = download_env()
    assert env["SSL_CERT_FILE"].endswith("cacert.pem")


def test_download_env_respects_existing_setting(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/custom/ca.pem")
    assert download_env()["SSL_CERT_FILE"] == "/custom/ca.pem"
