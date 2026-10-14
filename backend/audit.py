"""Before/after record of what a person changed. Sync and rules write their
own logs; this is for the edits made by hand, so "who set this to business
and when" always has an answer."""

import json

import config


def log(conn, actor, entity, entity_id, action, before=None, after=None):
    conn.execute("""INSERT INTO audit_log (at, actor, entity, entity_id, action, before_json, after_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                 (config.iso_now(), actor or "", entity, int(entity_id), action,
                  json.dumps(before, default=str) if before is not None else None,
                  json.dumps(after, default=str) if after is not None else None))
