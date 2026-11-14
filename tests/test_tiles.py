import urllib.request

from gui.tiles import TileServer


def test_tile_server_serves_cached_tile_and_leaflet(tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    f = tmp_path / "3" / "2" / "1.png"
    f.parent.mkdir(parents=True)
    f.write_bytes(png)
    srv = TileServer(tmp_path)
    assert urllib.request.urlopen(f"{srv.base_url}/tiles/3/2/1.png", timeout=5).read() == png
    assert b"Leaflet" in urllib.request.urlopen(f"{srv.base_url}/leaflet/leaflet.js", timeout=5).read()[:400]
    url = srv.put_page("t.html", "<html>hi</html>")
    assert urllib.request.urlopen(url, timeout=5).read() == b"<html>hi</html>"
    srv.httpd.shutdown()
