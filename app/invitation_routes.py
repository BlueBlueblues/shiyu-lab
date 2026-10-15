"""Administrator invitation UI, using the existing identity and invitation tables."""
import sqlite3
import time
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field

from app.auth import require_admin
from app.db import get_db
from app.invitations import create_invitation
from app.passwords import LEGACY_ADMIN_ID
from app.schemas import ContractModel, DataResponse


def require_invitation_admin(request: Request, identity: Annotated[str, Depends(require_admin)]):
    if request.state.current_user['id'] != LEGACY_ADMIN_ID:
        raise HTTPException(403, '只有管理员可以管理邀请。')


router = APIRouter(dependencies=[Depends(require_invitation_admin)])
Database = Annotated[sqlite3.Connection, Depends(get_db, scope='function')]


class InvitationCreate(ContractModel):
    expires_hours: Annotated[int, Field(strict=True, ge=1, le=8760)] = 24


def invitation_data(row):
    record = dict(row)
    record['status'] = ('revoked' if record['revoked_at'] is not None else
                        'used' if record['used_count'] >= record['max_uses'] else
                        'expired' if record['expires_at'] <= int(time.time()) else 'active')
    return record


def invitation_record(connection, identity):
    row = connection.execute('''SELECT id,created_at,expires_at,max_uses,used_count,revoked_at,last_used_at
        FROM invitations WHERE id=?''', (identity,)).fetchone()
    if row is None:
        raise HTTPException(404, '邀请记录不存在。')
    return row


@router.get('/admin/invitations', include_in_schema=False)
def invitation_page(request: Request):
    response = request.app.state.templates.TemplateResponse(
        request=request, name='invitations.html', context={'registration_open': request.app.state.settings.invite_registration_enabled})
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/api/admin/invitations', response_model=DataResponse[list[dict]])
def list_invitations(connection: Database, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    rows = connection.execute('''SELECT id,created_at,expires_at,max_uses,used_count,revoked_at,last_used_at
        FROM invitations ORDER BY created_at DESC,rowid DESC LIMIT 100''').fetchall()
    return {'data': [invitation_data(row) for row in rows]}


@router.post('/api/admin/invitations', status_code=201, response_model=DataResponse[dict])
def make_invitation(body: InvitationCreate, request: Request, response: Response, connection: Database):
    identity, token = create_invitation(connection, expires_hours=body.expires_hours, max_uses=1)
    record = invitation_data(invitation_record(connection, identity))
    record['link'] = request.app.state.settings.public_base_url.rstrip('/') + '/register?invite=' + quote(token, safe='')
    response.headers['Cache-Control'] = 'no-store'
    return {'data': record}


@router.post('/api/admin/invitations/{invitation_id}/revoke', response_model=DataResponse[dict])
def revoke_invitation(invitation_id: str, body: ContractModel, response: Response, connection: Database):
    invitation_record(connection, invitation_id)
    connection.execute('UPDATE invitations SET revoked_at=COALESCE(revoked_at,?) WHERE id=?',
                       (int(time.time()), invitation_id))
    response.headers['Cache-Control'] = 'no-store'
    return {'data': invitation_data(invitation_record(connection, invitation_id))}
