import os
import tempfile

import pytest

os.environ["PLT_DATA_DIR"] = tempfile.mkdtemp(prefix="plt-test-")


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    from playlist_transfer import providers, store
    from playlist_transfer.providers.demo import demo_providers
    from playlist_transfer.providers.files import FileProvider

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    reg = demo_providers()
    reg["file"] = FileProvider()
    providers.set_registry(reg)
    yield reg
