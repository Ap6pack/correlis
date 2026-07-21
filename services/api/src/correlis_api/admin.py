from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import StrEnum

from correlis_store import (
    CollectorRepository,
    ProjectionRepository,
    ProjectorFailureStatus,
    ProjectorIdentity,
    create_database_engine,
    create_session_factory,
)


def _jsonable(v):
    if is_dataclass(v):
        return {k: _jsonable(val) for k, val in asdict(v).items() if k != "token"}
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, StrEnum):
        return str(v)
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(val) for k, val in v.items()}
    return v


def _session_resources():
    url = os.getenv("CORRELIS_DATABASE_URL")
    if not url:
        raise RuntimeError("CORRELIS_DATABASE_URL is required")
    engine = create_database_engine(url)
    return engine, create_session_factory(engine)


def _dt(v):
    return datetime.fromisoformat(v) if v else None


def build_parser():
    p = argparse.ArgumentParser(prog="correlis-admin")
    sub = p.add_subparsers(dest="group", required=True)
    c = sub.add_parser("collectors").add_subparsers(dest="cmd", required=True)
    x = c.add_parser("create")
    x.add_argument("--tenant-id", required=True)
    x.add_argument("--name", required=True)
    x.add_argument("--source", required=True)
    x.add_argument("--collector-id")
    x = c.add_parser("list")
    x.add_argument("--tenant-id")
    x.add_argument("--limit", type=int, default=100)
    for n in ("enable", "disable"):
        x = c.add_parser(n)
        x.add_argument("--tenant-id", required=True)
        x.add_argument("--collector-id", required=True)
    cr = sub.add_parser("credentials").add_subparsers(dest="cmd", required=True)
    x = cr.add_parser("issue")
    x.add_argument("--tenant-id", required=True)
    x.add_argument("--collector-id", required=True)
    x.add_argument("--name", required=True)
    x.add_argument("--expires-at")
    x = cr.add_parser("list")
    x.add_argument("--tenant-id", required=True)
    x.add_argument("--collector-id", required=True)
    x = cr.add_parser("revoke")
    x.add_argument("--credential-id", required=True)
    a = sub.add_parser("auth-events").add_subparsers(dest="cmd", required=True)
    x = a.add_parser("list")
    x.add_argument("--tenant-id", required=True)
    x.add_argument("--collector-id")
    x.add_argument("--limit", type=int, default=100)
    pr = sub.add_parser("projectors").add_subparsers(dest="cmd", required=True)
    x = pr.add_parser("register")
    x.add_argument("--name", required=True)
    x.add_argument("--version", required=True)
    x = pr.add_parser("list")
    x.add_argument("--limit", type=int, default=100)
    for n in ("show", "pause", "resume"):
        x = pr.add_parser(n)
        x.add_argument("--name", required=True)
        x.add_argument("--version", required=True)
    pf = sub.add_parser("projection-failures").add_subparsers(dest="cmd", required=True)
    x = pf.add_parser("list")
    x.add_argument("--name", required=True)
    x.add_argument("--version", required=True)
    x.add_argument("--status", choices=["active", "resolved", "all"], default="active")
    x.add_argument("--limit", type=int, default=100)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    engine = None
    try:
        engine, sf = _session_resources()
        r = CollectorRepository(sf)
        projections = ProjectionRepository(sf)
        if args.group == "collectors" and args.cmd == "create":
            out = r.create_collector(
                tenant_id=args.tenant_id,
                name=args.name,
                source=args.source,
                collector_id=args.collector_id,
            )
        elif args.group == "collectors" and args.cmd == "list":
            out = r.list_collectors(tenant_id=args.tenant_id, limit=args.limit)
        elif args.group == "collectors" and args.cmd == "enable":
            out = r.enable_collector(args.tenant_id, args.collector_id)
        elif args.group == "collectors" and args.cmd == "disable":
            out = r.disable_collector(args.tenant_id, args.collector_id)
        elif args.group == "credentials" and args.cmd == "issue":
            pepper = os.getenv("CORRELIS_CREDENTIAL_PEPPER")
            out = r.issue_credential(
                args.tenant_id,
                args.collector_id,
                name=args.name,
                pepper=pepper,
                expires_at=_dt(args.expires_at),
            )
            print(
                json.dumps(
                    {
                        "credential": _jsonable(out.credential),
                        "token": out.token,
                        "warning": "Store this token now; it cannot be retrieved again.",
                    },
                    sort_keys=True,
                )
            )
            return 0
        elif args.group == "credentials" and args.cmd == "list":
            out = r.list_credentials(args.tenant_id, args.collector_id)
        elif args.group == "credentials" and args.cmd == "revoke":
            out = r.revoke_credential(args.credential_id)
        elif args.group == "auth-events" and args.cmd == "list":
            out = r.list_auth_events(
                tenant_id=args.tenant_id, collector_id=args.collector_id, limit=args.limit
            )
        elif args.group == "projectors" and args.cmd == "register":
            out = projections.register_projector(ProjectorIdentity(args.name, args.version))
        elif args.group == "projectors" and args.cmd == "list":
            out = projections.list_checkpoints(limit=args.limit)
        elif args.group == "projectors" and args.cmd == "show":
            out = projections.get_checkpoint(ProjectorIdentity(args.name, args.version))
            if out is None:
                raise RuntimeError("projector is not registered")
        elif args.group == "projectors" and args.cmd == "pause":
            out = projections.pause_projector(ProjectorIdentity(args.name, args.version))
        elif args.group == "projectors" and args.cmd == "resume":
            out = projections.resume_projector(ProjectorIdentity(args.name, args.version))
        elif args.group == "projection-failures" and args.cmd == "list":
            status = None if args.status == "all" else ProjectorFailureStatus(args.status)
            out = projections.list_failures(
                ProjectorIdentity(args.name, args.version), status=status, limit=args.limit
            )
        else:
            raise RuntimeError("unsupported command")
        print(json.dumps(_jsonable(out), sort_keys=True))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
