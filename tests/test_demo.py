import socket
from types import SimpleNamespace

import demo


def test_free_port_skips_a_busy_one():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        assert demo.free_port(port, tries=5) != port


def test_no_app_running_means_none():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unused = sock.getsockname()[1]
    assert demo.running_app_url(unused) is None


def test_idle_time_counts_from_the_last_page_visit_but_not_during_a_search():
    server = SimpleNamespace(_scrapes_in_progress=set(), last_page_contact=1000)
    assert demo.idle_for(server, now=1600) == 600

    server._scrapes_in_progress.add(("skincare", "IN"))
    assert demo.idle_for(server, now=1600) == 0


def test_packaged_app_uses_its_own_files_and_a_user_data_folder(tmp_path, monkeypatch):
    bundle, data = tmp_path / "bundle", tmp_path / "AppData" / "Creative Lens"
    (bundle / "models").mkdir(parents=True)
    for name in ("design_classifier.json", "rank_model_v1.json"):
        (bundle / "models" / name).write_bytes(b"model " + name.encode())
    for name in ("CREATIVE_LENS_DATA_DIR", "CREATIVE_LENS_CLIP_DIR", "CREATIVE_LENS_TESSERACT", "TESSDATA_PREFIX"):
        monkeypatch.setenv(name, "x")  # so monkeypatch restores whatever the function sets
        monkeypatch.delenv(name)

    assert demo.use_packaged_files(bundle, data) == data

    assert (data / "rank_model_v1.json").read_bytes() == b"model rank_model_v1.json"
    assert demo.os.environ["CREATIVE_LENS_DATA_DIR"] == str(data)
    assert demo.os.environ["CREATIVE_LENS_CLIP_DIR"] == str(bundle / "clip")
    assert demo.os.environ["CREATIVE_LENS_TESSERACT"] == str(bundle / "tesseract" / "tesseract.exe")
    assert demo.os.environ["TESSDATA_PREFIX"] == str(bundle / "tesseract" / "tessdata")
