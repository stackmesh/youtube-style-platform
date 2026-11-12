from fastapi import Request


def client_ip(request: Request) -> str:
    """Use the last forwarded hop. Caddy appends the real client address."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip() or "unknown"
    if request.client is None:
        return "unknown"
    return request.client.host
