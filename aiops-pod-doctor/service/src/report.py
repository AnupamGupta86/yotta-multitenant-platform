"""Turn one RCA report into an e-mail (subject, plain text and HTML)."""

from __future__ import annotations

from email.message import EmailMessage
from html import escape


def subject(report: dict) -> str:
    return f"[AIOps] {report['reason']} in {report['namespace']}/{report['pod']}"


def _summary_rows(report: dict) -> list[tuple[str, str]]:
    return [
        ("Pod", f"{report['namespace']}/{report['pod']}"),
        ("Container", report["container"]),
        ("Failure", report["reason"]),
        ("Status message", report.get("message") or "-"),
        ("Restarts", str(report["restart_count"])),
        ("Detected at", report["time"]),
        ("Analysed by", report["analysed_by"]),
    ]


def render_text(report: dict) -> str:
    rows = "\n".join(f"{name:15} {value}" for name, value in _summary_rows(report))
    return (
        f"AIOps root-cause report\n\n{rows}\n\n"
        f"== Analysis ==\n{report['analysis']}\n\n"
        f"== Pod details ==\n{report.get('pod_details', '-')}\n\n"
        f"== Recent events ==\n{report.get('events', '-')}\n\n"
        f"== Last log lines (secrets masked) ==\n{report.get('log_excerpt', '-')}\n"
    )


def render_html(report: dict) -> str:
    rows = "".join(
        f"<tr><th align='left' style='padding:4px 12px 4px 0'>{escape(name)}</th><td>{escape(value)}</td></tr>"
        for name, value in _summary_rows(report)
    )

    def block(title: str, text: str) -> str:
        return (
            f"<h3>{escape(title)}</h3>"
            f"<pre style='background:#f6f8fa;padding:12px;white-space:pre-wrap'>{escape(text or '-')}</pre>"
        )

    return (
        "<html><body style='font-family:Arial,sans-serif'>"
        f"<h2>AIOps root-cause report: {escape(report['namespace'])}/{escape(report['pod'])}</h2>"
        f"<table>{rows}</table>"
        + block("Analysis", report["analysis"])
        + block("Pod details", report.get("pod_details", ""))
        + block("Recent events", report.get("events", ""))
        + block("Last log lines (secrets masked)", report.get("log_excerpt", ""))
        + "<p style='color:#666'>Sent by aiops-pod-doctor (Kubernetes CronJob).</p></body></html>"
    )


def build_email(report: dict, sender: str, recipients: tuple[str, ...]) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject(report)
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(render_text(report))
    message.add_alternative(render_html(report), subtype="html")
    return message
