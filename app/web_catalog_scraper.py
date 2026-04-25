from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SUPPORTED_PRODUCT_HOSTS = {
    "store.anycubic.com": "Anycubic",
    "www.elegoo.com": "ELEGOO",
}


class _StructuredHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._ignore_depth = 0
        self._current_heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self._in_table = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._in_cell = False
        self._cell_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style"}:
            self._ignore_depth += 1
            return
        if self._ignore_depth:
            return

        if normalized in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._current_heading_tag = normalized
            self._heading_parts = []
            return

        if normalized == "table":
            self._in_table = True
            self._current_table = []
            return

        if self._in_table and normalized == "tr":
            self._current_row = []
            return

        if self._in_table and normalized in {"td", "th"}:
            self._in_cell = True
            self._cell_parts = []
            return

        if normalized == "br":
            if self._current_heading_tag:
                self._heading_parts.append(" ")
            if self._in_cell:
                self._cell_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style"}:
            self._ignore_depth = max(0, self._ignore_depth - 1)
            return
        if self._ignore_depth:
            return

        if normalized == self._current_heading_tag:
            heading = _collapse_whitespace("".join(self._heading_parts))
            if heading:
                self.headings.append(heading)
            self._current_heading_tag = None
            self._heading_parts = []
            return

        if self._in_table and normalized in {"td", "th"}:
            cell = _collapse_whitespace("".join(self._cell_parts))
            self._current_row.append(cell)
            self._in_cell = False
            self._cell_parts = []
            return

        if self._in_table and normalized == "tr":
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = []
            return

        if normalized == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False
            self._current_table = []

    def handle_data(self, data: str) -> None:
        if self._ignore_depth:
            return
        if self._current_heading_tag:
            self._heading_parts.append(data)
        if self._in_cell:
            self._cell_parts.append(data)


def scrape_supported_catalog_pages(
    *,
    urls: list[str],
    timeout_s: float = 20.0,
    fetch_html: Callable[[str, float], tuple[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cleaned_urls = [str(item).strip() for item in urls if str(item).strip()]
    if not cleaned_urls:
        raise ValueError("At least one supported vendor URL is required.")

    fetcher = fetch_html or _fetch_html_page
    merged_payload: dict[str, list[dict[str, Any]]] = {"printers": [], "resins": [], "profiles": []}
    normalized_urls: list[str] = []
    scraped_urls: list[str] = []
    unsupported_urls: list[str] = []
    notes: list[str] = []

    for candidate in cleaned_urls:
        html, normalized_url = fetcher(candidate, timeout_s)
        normalized_urls.append(normalized_url)
        page_payload = parse_supported_catalog_page(url=normalized_url, html=html)
        rows_scraped = sum(len(page_payload[key]) for key in ("printers", "resins", "profiles"))
        if rows_scraped == 0:
            unsupported_urls.append(normalized_url)
            continue
        scraped_urls.append(normalized_url)
        merged_payload = _merge_payloads(merged_payload, page_payload)

    if not any(merged_payload[key] for key in ("printers", "resins", "profiles")):
        raise ValueError(
            "None of the provided URLs matched a supported scraper. Supported pages currently include Anycubic product pages and the Anycubic resin settings guide."
        )

    if unsupported_urls:
        notes.append(
            f"Ignored {len(unsupported_urls)} unsupported page(s); supported sources currently include Anycubic product pages and the Anycubic resin settings guide."
        )

    return merged_payload, {
        "normalized_urls": normalized_urls,
        "scraped_urls": scraped_urls,
        "unsupported_urls": unsupported_urls,
        "notes": notes,
    }


def parse_supported_catalog_page(*, url: str, html: str) -> dict[str, list[dict[str, Any]]]:
    normalized_url = _normalize_scrape_url(url)
    parsed = urlparse(normalized_url)
    host = (parsed.hostname or "").strip().lower()
    path = parsed.path.rstrip("/")
    retrieved_at = _iso_timestamp()

    if host == "store.anycubic.com" and path == "/blogs/news/resin-settings-for-anycubic-3d-printers":
        return _parse_anycubic_resin_settings_guide(
            html=html,
            url=normalized_url,
            retrieved_at=retrieved_at,
        )

    if host in _SUPPORTED_PRODUCT_HOSTS and "/products/" in path:
        return _parse_vendor_product_page(
            html=html,
            url=normalized_url,
            brand=_SUPPORTED_PRODUCT_HOSTS[host],
            retrieved_at=retrieved_at,
        )

    return {"printers": [], "resins": [], "profiles": []}


def _parse_anycubic_resin_settings_guide(*, html: str, url: str, retrieved_at: str) -> dict[str, list[dict[str, Any]]]:
    parser = _StructuredHtmlParser()
    parser.feed(html)
    headings = [item for item in parser.headings if "printing settings for" in item.lower()]
    tables = [table for table in parser.tables if _looks_like_anycubic_settings_table(table)]

    payload: dict[str, list[dict[str, Any]]] = {"printers": [], "resins": [], "profiles": []}
    table_index = 0
    for heading in headings:
        if table_index >= len(tables):
            break
        printer_name, mode_name = _parse_settings_heading(heading)
        if not printer_name:
            continue
        printer_name = _normalize_vendor_printer_name(printer_name, "Anycubic")

        payload["printers"].append(
            {
                "name": printer_name,
                "brand": "Anycubic",
                "model": _strip_brand_prefix(printer_name, "Anycubic"),
                "technology": "MSLA",
                "metadata": _metadata_for_source(
                    url=url,
                    retrieved_at=retrieved_at,
                    parser_id="anycubic_resin_settings_guide",
                    extra={"source_heading": heading},
                ),
            }
        )

        rows = tables[table_index]
        table_index += 1
        header = [_collapse_whitespace(cell) for cell in rows[0]]
        material_names = [item for item in header[1:] if item]
        row_map: dict[str, list[str]] = {}
        for row in rows[1:]:
            if not row:
                continue
            label = _collapse_whitespace(row[0]).lower()
            if not label:
                continue
            row_map[label] = row[1:]

        for idx, material_name in enumerate(material_names):
            payload["resins"].append(
                {
                    "name": material_name,
                    "brand": "Anycubic",
                    "material_type": "resin",
                    "material_family": _infer_material_family(material_name, "resin") or material_name,
                    "technical_goal": _infer_material_goal(material_name),
                    "metadata": _metadata_for_source(
                        url=url,
                        retrieved_at=retrieved_at,
                        parser_id="anycubic_resin_settings_guide",
                        extra={"source_heading": heading},
                    ),
                }
            )

            layer_height = _parse_scalar(_value_at(row_map.get("layer thickness"), idx))
            exposure = _parse_scalar(_value_at(row_map.get("exposure time"), idx))
            bottom_exposure = _parse_scalar(_value_at(row_map.get("bottom exposure time"), idx))
            light_off_time = _parse_scalar(_value_at(row_map.get("light-off time"), idx))
            z_lift_distance = _parse_scalar(_value_at(row_map.get("z lift distance"), idx))
            z_lift_speed = _parse_scalar(_value_at(row_map.get("z lift speed"), idx))
            z_retract_speed = _parse_scalar(_value_at(row_map.get("z retract speed"), idx))
            bottom_layer_count = _parse_int(_value_at(row_map.get("bottom layer"), idx))
            anti_aliasing_level = _parse_int(_value_at(row_map.get("anti-aliasing level"), idx))
            if all(value is None for value in (layer_height, exposure, bottom_exposure, light_off_time)):
                continue

            profile_name = mode_name or "Official Settings"
            payload["profiles"].append(
                {
                    "printer_name": printer_name,
                    "resin_name": material_name,
                    "profile_name": profile_name,
                    "layer_height_mm": layer_height,
                    "exposure_s": exposure,
                    "bottom_exposure_s": bottom_exposure,
                    "rest_time_before_print_s": light_off_time,
                    "is_default": mode_name is None or "standard" in profile_name.lower(),
                    "is_active": True,
                    "notes": "Scraped from Anycubic official resin settings guide.",
                    "metadata": _metadata_for_source(
                        url=url,
                        retrieved_at=retrieved_at,
                        parser_id="anycubic_resin_settings_guide",
                        extra={
                            "source_heading": heading,
                            "settings_mode": mode_name,
                            "bottom_layer_count": bottom_layer_count,
                            "z_lift_distance_mm": z_lift_distance,
                            "z_lift_speed_mm_s": z_lift_speed,
                            "z_retract_speed_mm_s": z_retract_speed,
                            "anti_aliasing_level": anti_aliasing_level,
                        },
                    ),
                }
            )

    return _dedupe_payload(payload)


def _parse_vendor_product_page(*, html: str, url: str, brand: str, retrieved_at: str) -> dict[str, list[dict[str, Any]]]:
    lines = _html_to_lines(html)
    title = _extract_title(html, lines)
    if not title:
        return {"printers": [], "resins": [], "profiles": []}

    printer_like = _looks_like_printer_page(title, lines)
    material_like = _looks_like_material_page(title, lines) and not printer_like

    if material_like:
        material_type, material_family = _infer_material_identity(title, lines)
        nozzle_range = _parse_range(
            _extract_label_value(
                lines,
                [
                    "Printing Temperature",
                    "Recommended Printing Temperature",
                    "Nozzle Temperature",
                    "Extruder Temperature",
                ],
            )
        )
        bed_range = _parse_range(
            _extract_label_value(lines, ["Bed Temperature", "Hot Bed Temperature", "Recommended Bed Temperature"])
        )
        diameter_text = _extract_label_value(lines, ["Diameter", "Filament Diameter"])
        viscosity_text = _extract_label_value(lines, ["Viscosity"])
        density_text = _extract_label_value(lines, ["Density"])
        shrinkage_text = _extract_label_value(lines, ["Volume Shrinkage Rate", "Shrinkage"])
        hardness_text = _extract_label_value(lines, ["Hardness", "Shore Hardness"])

        metadata = _metadata_for_source(
            url=url,
            retrieved_at=retrieved_at,
            parser_id=f"{brand.lower()}_product_page",
            extra={
                "density_text": density_text,
                "viscosity_text": viscosity_text,
                "shrinkage_text": shrinkage_text,
            },
        )
        return {
            "printers": [],
            "resins": [
                {
                    "name": title,
                    "brand": brand,
                    "material_type": material_type,
                    "material_family": material_family,
                    "technical_goal": _infer_material_goal(title),
                    "viscosity_cp": _range_midpoint(_parse_range(viscosity_text)),
                    "shore_hardness": hardness_text,
                    "shrinkage_percent": _range_midpoint(_parse_range(shrinkage_text)),
                    "density_g_cm3": _parse_scalar(density_text),
                    "filament_diameter_mm": _parse_scalar(diameter_text),
                    "nozzle_temp_min_c": nozzle_range[0],
                    "nozzle_temp_max_c": nozzle_range[1],
                    "bed_temp_c": _range_midpoint(bed_range) or bed_range[1] or bed_range[0],
                    "notes": "Scraped from supported vendor material page.",
                    "metadata": metadata,
                }
            ],
            "profiles": [],
        }

    if not printer_like:
        return {"printers": [], "resins": [], "profiles": []}

    build_volume_text = _extract_label_value(lines, ["Printing Volume", "Build Volume", "Build Volume (W x D x H)"])
    xy_resolution_text = _extract_label_value(lines, ["X/Y Axis Resolution", "XY Resolution"])
    material_compatibility = _extract_label_value(lines, ["Material Compatibility", "Resin", "Materials"])
    file_transfer = _extract_label_value(lines, ["File Transfer", "Connection Method", "Connectivity"])
    slicing_software = _extract_label_value(lines, ["Slicing Software", "Slicer Software", "Software"])
    technology = _infer_printer_technology(title, lines)
    build_volume = _parse_build_volume(build_volume_text)
    xy_resolution = _parse_xy_resolution(xy_resolution_text)

    metadata = _metadata_for_source(
        url=url,
        retrieved_at=retrieved_at,
        parser_id=f"{brand.lower()}_product_page",
        extra={
            "xy_resolution_text": xy_resolution_text,
            "material_compatibility": material_compatibility,
            "file_transfer": file_transfer,
            "slicing_software": slicing_software,
        },
    )
    return {
        "printers": [
            {
                "name": title,
                "brand": brand,
                "model": _strip_brand_prefix(title, brand),
                "technology": technology,
                "xy_resolution_um": xy_resolution,
                "build_volume_x_mm": build_volume[0],
                "build_volume_y_mm": build_volume[1],
                "build_volume_z_mm": build_volume[2],
                "tilt_release_supported": any("tilt release" in line.lower() for line in lines),
                "wifi_supported": _contains_any(lines, ["wifi", "wi-fi"]),
                "notes": "Scraped from supported vendor printer page.",
                "metadata": metadata,
            }
        ],
        "resins": [],
        "profiles": [],
    }


def _fetch_html_page(url: str, timeout_s: float) -> tuple[str, str]:
    normalized_url = _normalize_scrape_url(url)
    request = Request(
        normalized_url,
        headers={
            "User-Agent": "ResinLogic-AI/0.1",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        },
    )
    with urlopen(request, timeout=timeout_s) as response:  # noqa: S310
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")
    return body, normalized_url


def _normalize_scrape_url(url: str) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        raise ValueError("Scrape URL is required.")

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Scrape URL must use http or https.")
    if parsed.username or parsed.password:
        raise ValueError("Scrape URL must not include embedded credentials.")

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValueError("Scrape URL must include a hostname.")
    if hostname in _LOCAL_HOSTS or hostname.endswith(".local"):
        raise ValueError("Scrape URL must not point to localhost.")

    try:
        parsed_ip = ip_address(hostname)
    except ValueError:
        parsed_ip = None
    if parsed_ip and (
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_link_local
        or parsed_ip.is_multicast
        or parsed_ip.is_reserved
        or parsed_ip.is_unspecified
    ):
        raise ValueError("Scrape URL must not point to a private or local IP address.")

    return parsed._replace(fragment="").geturl()


def _looks_like_anycubic_settings_table(rows: list[list[str]]) -> bool:
    if not rows or not rows[0]:
        return False
    first = _collapse_whitespace(rows[0][0]).lower()
    return first in {"resin type", "material type"} or any(
        cell and "resin" in cell.lower() for cell in rows[0][1:]
    )


def _parse_settings_heading(heading: str) -> tuple[str | None, str | None]:
    cleaned = _collapse_whitespace(heading)
    match = re.search(r"printing settings for\s+(?P<printer>.+?)(?:\s*\((?P<mode>[^)]+)\))?$", cleaned, flags=re.I)
    if not match:
        return None, None
    printer_name = _collapse_whitespace(match.group("printer"))
    mode_name = _collapse_whitespace(match.group("mode") or "") or None
    return printer_name, mode_name


def _html_to_lines(html: str) -> list[str]:
    cleaned = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    cleaned = re.sub(r"(?i)</?(?:h[1-6]|p|div|section|article|li|ul|ol|table|tbody|thead|tr|td|th|br|span)[^>]*>", "\n", cleaned)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    text = unescape(cleaned)
    lines: list[str] = []
    for raw in text.splitlines():
        collapsed = _collapse_whitespace(raw)
        if collapsed:
            lines.append(collapsed)
    return lines


def _extract_title(html: str, lines: list[str]) -> str:
    match = re.search(r"<h1\b[^>]*>(.*?)</h1>", html, flags=re.I | re.S)
    if match:
        title = _collapse_whitespace(re.sub(r"(?s)<[^>]+>", " ", unescape(match.group(1))))
        if title:
            return title
    title_tag = re.search(r"<title\b[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    if title_tag:
        title = _collapse_whitespace(re.sub(r"(?s)<[^>]+>", " ", unescape(title_tag.group(1))))
        if title:
            return title.split("|")[0].strip()
    return lines[0] if lines else ""


def _extract_label_value(lines: list[str], labels: list[str]) -> str | None:
    normalized_labels = [label.strip().lower() for label in labels]
    for idx, line in enumerate(lines):
        lowered = line.lower()
        for label in normalized_labels:
            if lowered == label or lowered.startswith(f"{label} ") or lowered.startswith(f"{label}:"):
                remainder = line[len(label) :].strip(" :")
                if remainder:
                    return remainder
                for candidate in lines[idx + 1 : idx + 4]:
                    if candidate:
                        return candidate
    return None


def _looks_like_printer_page(title: str, lines: list[str]) -> bool:
    title_lower = title.lower()
    if any(keyword in title_lower for keyword in ("photon", "mars", "saturn", "jupiter", "kobra", "neptune", "centauri", "form ")):
        return True
    text_blob = "\n".join(lines).lower()
    return any(keyword in text_blob for keyword in ("build volume", "printing volume", "xy resolution", "printing technology"))


def _looks_like_material_page(title: str, lines: list[str]) -> bool:
    title_lower = title.lower()
    if any(keyword in title_lower for keyword in (" resin", "resin ", "filament", "pla", "petg", "abs", "asa", "tpu", "nylon")):
        return True
    text_blob = "\n".join(lines).lower()
    return any(keyword in text_blob for keyword in ("viscosity", "shore hardness", "tensile strength", "printing temperature", "bed temperature"))


def _infer_printer_technology(title: str, lines: list[str]) -> str | None:
    explicit = _extract_label_value(lines, ["Printing Technology", "Technology"])
    if explicit:
        lowered = explicit.lower()
        if "msla" in lowered or "stereolithography" in lowered or "resin" in lowered:
            return "MSLA"
        if "fdm" in lowered or "filament" in lowered:
            return "FDM"

    title_lower = title.lower()
    if any(keyword in title_lower for keyword in ("photon", "mars", "saturn", "jupiter", "form ")):
        return "MSLA"
    if any(keyword in title_lower for keyword in ("kobra", "neptune", "centauri", "filament")):
        return "FDM"
    return None


def _infer_material_identity(title: str, lines: list[str]) -> tuple[str, str | None]:
    text = f"{title}\n" + "\n".join(lines)
    lowered = text.lower()
    if any(keyword in lowered for keyword in ("pla", "petg", "abs", "asa", "tpu", "nylon", "filament")):
        return "filament", _infer_filament_family(text) or _infer_material_family(text, "filament")
    return "resin", _infer_material_family(text, "resin")


def _infer_filament_family(text: str) -> str | None:
    lowered = text.lower()
    for family, pattern in (
        ("PLA", r"\bpla\b"),
        ("PETG", r"\bpetg\b"),
        ("ABS", r"\babs\b"),
        ("ASA", r"\basa\b"),
        ("TPU", r"\btpu\b"),
        ("Nylon", r"\bnylon\b"),
    ):
        if re.search(pattern, lowered):
            return family
    return None


def _infer_material_family(text: str, material_type: str) -> str | None:
    family_patterns = [
        ("ABS-Like", r"abs[- ]like"),
        ("Water-Wash", r"water[- ]wash"),
        ("Plant-Based", r"plant[- ]based"),
        ("High Speed", r"high speed"),
        ("Tough", r"\btough\b"),
        ("Standard", r"\bstandard\b"),
        ("PLA", r"\bpla\b"),
        ("PETG", r"\bpetg\b"),
        ("ABS", r"\babs\b"),
        ("ASA", r"\basa\b"),
        ("TPU", r"\btpu\b"),
        ("Nylon", r"\bnylon\b"),
    ]
    lowered = text.lower()
    for family, pattern in family_patterns:
        if re.search(pattern, lowered):
            return family
    if material_type == "resin" and "resin" in lowered:
        return "Resin"
    if material_type == "filament" and "filament" in lowered:
        return "Filament"
    return None


def _infer_material_goal(text: str) -> str | None:
    lowered = text.lower()
    if "high speed" in lowered:
        return "high_speed_printing"
    if "abs-like" in lowered or "tough" in lowered:
        return "durability"
    if "clear" in lowered:
        return "optical_clarity"
    if "water-wash" in lowered:
        return "easy_cleanup"
    if "pla" in lowered:
        return "general_purpose"
    if "petg" in lowered:
        return "durable_general_purpose"
    return None


def _parse_build_volume(text: str | None) -> tuple[float | None, float | None, float | None]:
    if not text:
        return None, None, None
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*mm",
        text,
        flags=re.I,
    )
    if not match:
        return None, None, None
    return float(match.group(1)), float(match.group(2)), float(match.group(3))


def _parse_xy_resolution(text: str | None) -> int | None:
    if not text:
        return None
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None
    return int(round(max(numbers[:2])))


def _parse_range(text: str | None) -> tuple[float | None, float | None]:
    if not text:
        return None, None
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return numbers[0], numbers[1]


def _range_midpoint(values: tuple[float | None, float | None]) -> float | None:
    low, high = values
    if low is None and high is None:
        return None
    if low is None:
        return high
    if high is None:
        return low
    return round((low + high) / 2.0, 3)


def _parse_scalar(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned in {"/", "-", "n/a", "N/A"}:
        return None
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    return float(match.group(0))


def _parse_int(text: str | None) -> int | None:
    value = _parse_scalar(text)
    if value is None:
        return None
    return int(round(value))


def _value_at(values: list[str] | None, index: int) -> str | None:
    if values is None or index >= len(values):
        return None
    return values[index]


def _metadata_for_source(*, url: str, retrieved_at: str, parser_id: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = {
        "source_urls": [url],
        "retrieved_at": retrieved_at,
        "scraper": parser_id,
        "source_type": "supported_web_scrape",
    }
    if extra:
        metadata.update({key: value for key, value in extra.items() if value not in (None, "", [], {})})
    return metadata


def _merge_payloads(left: dict[str, list[dict[str, Any]]], right: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    merged = {
        "printers": [*left.get("printers", []), *right.get("printers", [])],
        "resins": [*left.get("resins", []), *right.get("resins", [])],
        "profiles": [*left.get("profiles", []), *right.get("profiles", [])],
    }
    return _dedupe_payload(merged)


def _dedupe_payload(payload: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    printers = _dedupe_named_rows(payload.get("printers", []))
    resins = _dedupe_named_rows(payload.get("resins", []))
    profiles = _dedupe_profile_rows(payload.get("profiles", []))
    return {"printers": printers, "resins": resins, "profiles": profiles}


def _dedupe_named_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = _collapse_whitespace(str(row.get("name") or ""))
        if not name:
            continue
        key = name.lower()
        if key in by_name:
            by_name[key] = _merge_record(by_name[key], row)
        else:
            by_name[key] = row
    return list(by_name.values())


def _dedupe_profile_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        printer_name = _collapse_whitespace(str(row.get("printer_name") or ""))
        resin_name = _collapse_whitespace(str(row.get("resin_name") or ""))
        profile_name = _collapse_whitespace(str(row.get("profile_name") or ""))
        if not printer_name or not resin_name or not profile_name:
            continue
        key = (printer_name.lower(), resin_name.lower(), profile_name.lower())
        if key in by_key:
            by_key[key] = _merge_record(by_key[key], row)
        else:
            by_key[key] = row
    return list(by_key.values())


def _merge_record(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if key == "metadata":
            merged[key] = _merge_metadata(left.get("metadata"), value)
            continue
        if key == "notes":
            merged[key] = _merge_notes(left.get("notes"), value)
            continue
        if value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _merge_metadata(left: Any, right: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if isinstance(left, dict):
        merged.update(left)
    if isinstance(right, dict):
        for key, value in right.items():
            if key == "source_urls":
                urls = []
                for candidate in [*(merged.get("source_urls") or []), *(value or [])]:
                    text = str(candidate).strip()
                    if text and text not in urls:
                        urls.append(text)
                merged["source_urls"] = urls
            elif value not in (None, "", [], {}):
                merged[key] = value
    return merged


def _merge_notes(left: Any, right: Any) -> str | None:
    values: list[str] = []
    for candidate in (left, right):
        if isinstance(candidate, str):
            cleaned = _collapse_whitespace(candidate)
            if cleaned and cleaned not in values:
                values.append(cleaned)
    if not values:
        return None
    return " ".join(values)


def _contains_any(lines: list[str], needles: list[str]) -> bool:
    lowered = "\n".join(lines).lower()
    return any(needle.lower() in lowered for needle in needles)


def _strip_brand_prefix(name: str, brand: str) -> str:
    lowered_name = name.lower()
    lowered_brand = brand.lower()
    if lowered_name.startswith(lowered_brand + " "):
        return name[len(brand) + 1 :].strip()
    return name


def _normalize_vendor_printer_name(name: str, brand: str) -> str:
    if name.lower().startswith(brand.lower() + " "):
        return name
    return f"{brand} {name}"


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -:\u2022\t\r\n")


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()