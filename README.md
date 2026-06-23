# OpenWrt Router Agent MVP

Minimal outbound control-plane for OpenWrt routers.

## Install

Primary server-hosted install:

```sh
sh -c "$(wget -O - https://kasplex.store/install.sh)"
```

Fallback GitHub install:

```sh
sh -c "$(wget -O - https://raw.githubusercontent.com/NISIAKAS/openwrt-router-agent/main/install.sh)"
```

The install script is public and does not contain backend admin secrets. New routers register as `pending`; the backend does not issue tasks until an admin approves the device.

## Components

- `backend/` - FastAPI + SQLite control API.
- `agent/` - OpenWrt shell agent with procd init script.

## Safety Model

- Routers initiate outbound requests to the backend.
- No client public IP is required.
- The MVP agent does not expose arbitrary shell execution.
- Mutating actions are allowlisted.
- First deployment supports registration, heartbeat, snapshot, task polling, task results, and self-update skeleton.

## Backend API

- `GET /healthz`
- `POST /api/v1/register`
- `POST /api/v1/devices/{device_id}/heartbeat`
- `POST /api/v1/devices/{device_id}/snapshot`
- `GET /api/v1/devices/{device_id}/tasks/next`
- `POST /api/v1/devices/{device_id}/tasks/{task_id}/result`
- `GET /api/v1/admin/devices`
- `GET /api/v1/admin/devices/{device_id}`
- `POST /api/v1/admin/devices/{device_id}/approve`
- `POST /api/v1/admin/devices/{device_id}/revoke`
- `GET /api/v1/admin/devices/{device_id}/tasks`
- `POST /api/v1/admin/devices/{device_id}/tasks`

Admin endpoints require `X-Admin-Token`.
Device endpoints require `Authorization: Bearer <device_token>` after registration.
