from __future__ import annotations

from app.web_catalog_scraper import parse_supported_catalog_page, scrape_supported_catalog_pages


def test_parse_anycubic_printer_page_extracts_printer_details():
    html = """
    <html>
      <body>
        <h1>Anycubic Photon Mono M7 Pro</h1>
        <h4>Printing Volume</h4><p>223*126*230mm³ (6.5L)</p>
        <h4>X/Y Axis Resolution</h4><p>16.8 × 24.8μm</p>
        <h4>File Transfer</h4><p>USB/WiFi/LAN</p>
        <h4>Slicing Software</h4><p>Photon Workshop, Chitubox</p>
      </body>
    </html>
    """

    payload = parse_supported_catalog_page(
        url="https://store.anycubic.com/products/photon-mono-m7-pro",
        html=html,
    )

    assert len(payload["printers"]) == 1
    printer = payload["printers"][0]
    assert printer["name"] == "Anycubic Photon Mono M7 Pro"
    assert printer["technology"] == "MSLA"
    assert printer["build_volume_x_mm"] == 223.0
    assert printer["build_volume_y_mm"] == 126.0
    assert printer["build_volume_z_mm"] == 230.0
    assert printer["xy_resolution_um"] == 25
    assert printer["wifi_supported"] is True
    assert printer["metadata"]["source_urls"] == ["https://store.anycubic.com/products/photon-mono-m7-pro"]


def test_parse_anycubic_material_page_extracts_filament_fields():
    html = """
    <html>
      <body>
        <h1>Anycubic High Speed PLA Filament</h1>
        <p>Printing Temperature</p><p>190-220℃</p>
        <p>Hot Bed Temperature</p><p>45-60℃</p>
        <p>Diameter</p><p>1.75mm</p>
      </body>
    </html>
    """

    payload = parse_supported_catalog_page(
        url="https://store.anycubic.com/products/high-speed-pla-filament",
        html=html,
    )

    assert len(payload["resins"]) == 1
    material = payload["resins"][0]
    assert material["material_type"] == "filament"
    assert material["material_family"] == "PLA"
    assert material["nozzle_temp_min_c"] == 190.0
    assert material["nozzle_temp_max_c"] == 220.0
    assert material["bed_temp_c"] == 52.5
    assert material["filament_diameter_mm"] == 1.75


def test_parse_anycubic_resin_settings_guide_extracts_profiles():
    html = """
    <html>
      <body>
        <h3>Printing Settings for <a href="/products/photon-mono-m7-pro">Photon Mono M7 Pro</a></h3>
        <table>
          <tr><th>Resin Type</th><th>Standard Resin</th><th>ABS-Like Resin V2</th></tr>
          <tr><td>Layer Thickness</td><td>0.05mm</td><td>0.05mm</td></tr>
          <tr><td>Exposure Time</td><td>2s</td><td>2.2s</td></tr>
          <tr><td>Light-Off Time</td><td>0.5s</td><td>0.5s</td></tr>
          <tr><td>Bottom Exposure Time</td><td>25s</td><td>30s</td></tr>
          <tr><td>Bottom Layer</td><td>4</td><td>4</td></tr>
          <tr><td>Z Lift Distance</td><td>8mm</td><td>8mm</td></tr>
          <tr><td>Z Lift Speed</td><td>6mm/s</td><td>6mm/s</td></tr>
        </table>
      </body>
    </html>
    """

    payload = parse_supported_catalog_page(
        url="https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers",
        html=html,
    )

    assert len(payload["printers"]) == 1
    assert len(payload["resins"]) == 2
    assert len(payload["profiles"]) == 2
    profile = payload["profiles"][0]
    assert profile["printer_name"] == "Anycubic Photon Mono M7 Pro"
    assert profile["profile_name"] == "Official Settings"
    assert profile["layer_height_mm"] == 0.05
    assert profile["exposure_s"] == 2.0
    assert profile["bottom_exposure_s"] == 25.0
    assert profile["rest_time_before_print_s"] == 0.5
    assert profile["metadata"]["bottom_layer_count"] == 4


def test_scrape_supported_catalog_pages_merges_rows_from_multiple_urls():
    pages = {
        "https://store.anycubic.com/products/photon-mono-m7-pro": (
            "<html><body><h1>Anycubic Photon Mono M7 Pro</h1><h4>Printing Volume</h4><p>223*126*230mm³</p></body></html>",
            "https://store.anycubic.com/products/photon-mono-m7-pro",
        ),
        "https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers": (
            """
            <html><body>
              <h3>Printing Settings for Photon Mono M7 Pro</h3>
              <table>
                <tr><th>Resin Type</th><th>Standard Resin</th></tr>
                <tr><td>Layer Thickness</td><td>0.05mm</td></tr>
                <tr><td>Exposure Time</td><td>2s</td></tr>
                <tr><td>Bottom Exposure Time</td><td>25s</td></tr>
              </table>
            </body></html>
            """,
            "https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers",
        ),
    }

    def fake_fetch(url: str, timeout_s: float):
        assert timeout_s == 15.0
        return pages[url]

    payload, summary = scrape_supported_catalog_pages(
        urls=list(pages),
        timeout_s=15.0,
        fetch_html=fake_fetch,
    )

    assert len(payload["printers"]) == 1
    assert len(payload["resins"]) == 1
    assert len(payload["profiles"]) == 1
    assert summary["scraped_urls"] == list(pages)
    assert summary["unsupported_urls"] == []