from services.api.app.pilot import ensure_pilot_schema, pg_connection
from services.api.app.secrets_crypto import LEGACY_SCHEME, decrypt_secret, encrypt_secret


def main() -> int:
    converted = 0
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        rows = connection.execute(
            'SELECT id, webhook_url_encrypted, bot_token_encrypted FROM workspace_slack_integrations'
        ).fetchall()
        for row in rows:
            updates = {}
            if row.get('webhook_url_encrypted'):
                value, scheme = decrypt_secret(str(row['webhook_url_encrypted']))
                if scheme == LEGACY_SCHEME:
                    updates['webhook_url_encrypted'] = encrypt_secret(value)
            if row.get('bot_token_encrypted'):
                value, scheme = decrypt_secret(str(row['bot_token_encrypted']))
                if scheme == LEGACY_SCHEME:
                    updates['bot_token_encrypted'] = encrypt_secret(value)
            if updates:
                connection.execute(
                    'UPDATE workspace_slack_integrations SET webhook_url_encrypted = COALESCE(%s, webhook_url_encrypted), bot_token_encrypted = COALESCE(%s, bot_token_encrypted), updated_at = NOW() WHERE id = %s',
                    (updates.get('webhook_url_encrypted'), updates.get('bot_token_encrypted'), str(row['id'])),
                )
                converted += 1
        connection.commit()
    print(f'converted_rows={converted}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
