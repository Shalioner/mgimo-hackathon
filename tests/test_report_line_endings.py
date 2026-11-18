from pathlib import Path
import shutil

from report import main


def test_report_check_ignores_newline_style_but_detects_edits(tmp_path):
    fixture = Path(__file__).resolve().parent.parent / "projects" / "esim-travel"
    project = tmp_path / "esim-travel"
    shutil.copytree(fixture, project)
    assert main([str(project)]) == 0
    report = project / "outputs" / "report.html"
    lf = report.read_bytes().replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    cases = [
        (lf, 0),
        (crlf, 0),
        (crlf.replace(b"ALT-001", b"ALT-00X", 1), 1),
        (lf.replace(b"\n", b"\r", 1), 1),
    ]
    for content, expected in cases:
        report.write_bytes(content)
        assert main([str(project), "--check"]) == expected
        assert report.read_bytes() == content
