export type AgentStatus = {
	label: string;
	loaded: boolean;
	pid: number | null;
	last_exit_code: number | null;
};

export type StatusOut = {
	posts_today: number;
	posts_per_day_cap: number;
	uploads_enabled: boolean;
	last_uploaded_at: string | null;
	sessionid_days_remaining: number | null;
	post_tz: string;
	madrid_offset_hours: number;
	now_iso: string;
	agents: AgentStatus[];
	pending_count: number;
	under_review_count: number;
};

export type HealthOut = {
	ok: boolean;
	db_reachable: boolean;
	db_path: string;
	config_loaded: boolean;
	config_path: string;
	config_error: string | null;
	env_path_exists: boolean;
	cookies_exists: boolean;
	logs_dir: string;
	dev_mode: boolean;
	post_tz: string;
	madrid_offset_hours: number;
};

export type CookieHealthOut = {
	sessionid_days_remaining: number | null;
};

export type RenderOut = {
	post_id: string;
	title: string;
	subreddit: string;
	author: string;
	caption: string;
	video_path: string;
	cover_path: string;
	upload_status: string;
	upload_attempts: number;
	next_retry_at: string | null;
	telegram_msg_id: number | null;
	uploaded_at: string | null;
	tiktok_url: string | null;
};

export async function apiGet<T>(path: string): Promise<T> {
	const r = await fetch(path);
	if (!r.ok) throw new Error(`${path} → ${r.status}`);
	return (await r.json()) as T;
}

export type TomlOut = {
	path: string;
	content: string;
};

export type EnvEntry = {
	key: string;
	value_masked: string;
	is_secret: boolean;
};

export type EnvOut = {
	path: string;
	entries: EnvEntry[];
};

// ---- CSRF (P0.1) -----------------------------------------------------------
//
// Signed double-submit: /api/csrf sets an HttpOnly signed cookie (JS never
// reads it) and returns the matching plaintext token in the response body,
// which we cache in memory and echo back as X-CSRF-Token on every mutating
// request. Backend validates cookie vs header
// (webapp/backend/app.py::csrf_protect_middleware).
let _csrfToken: string | null = null;
let _csrfTokenPromise: Promise<string> | null = null;

async function _fetchCsrfToken(): Promise<string> {
	const r = await fetch('/api/csrf');
	if (!r.ok) throw new Error(`csrf token fetch failed: ${r.status}`);
	const j = (await r.json()) as { csrf_token: string };
	return j.csrf_token;
}

async function _getCsrfToken(): Promise<string> {
	if (_csrfToken) return _csrfToken;
	if (!_csrfTokenPromise) {
		_csrfTokenPromise = _fetchCsrfToken().then((t) => {
			_csrfToken = t;
			return t;
		});
	}
	return _csrfTokenPromise;
}

// Cookie has a max age (default 3600s) and the signing secret is
// per-boot in dev — a 403 on a mutating call means the token's stale,
// not that the request itself was wrong. Clear the cache so the next
// mutating call re-fetches instead of retrying with the same dead token.
function _invalidateCsrfToken(): void {
	_csrfToken = null;
	_csrfTokenPromise = null;
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
	const csrfToken = await _getCsrfToken();
	const r = await fetch(path, {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
		body: JSON.stringify(body)
	});
	if (!r.ok) {
		if (r.status === 403) _invalidateCsrfToken();
		let msg = `${r.status}`;
		try {
			const j = await r.json();
			if (j?.detail) msg = j.detail;
		} catch {
			// non-JSON body — keep status code
		}
		throw new Error(msg);
	}
	return (await r.json()) as T;
}

export type JobOut = {
	id: string;
	kind: 'render' | 'upload' | 'confirm';
	args: string[];
	started_at: string;
	ended_at: string | null;
	exit_code: number | null;
	running: boolean;
	line_count: number;
};

// ---- Schedule tab (mirrors webapp/backend/routers/schedule.py) --------

export type SlotOverrideValue = string | boolean | null;

export type SlotEffective = {
	instance: string;
	publish_hour: number;
	render_time: string;
	upload_time: string;
	render_enabled: boolean;
	upload_enabled: boolean;
	auto_approve: boolean;
	notify_render_pre: boolean;
	notify_render_crash: boolean;
	notify_render_empty: boolean;
	notify_upload_approval_card: boolean;
	notify_upload_force_approve: boolean;
	notify_upload_success: boolean;
	notify_upload_failure: boolean;
	notify_upload_gate_reject: boolean;
};

export type SlotView = {
	instance: string;
	defaults: Record<string, string | boolean | number>;
	overrides: Record<string, string>;
	effective: SlotEffective;
};

export type SlotsOut = {
	slots: SlotView[];
	helper_available: boolean;
};

export type OverrideIn = {
	overrides: Record<string, SlotOverrideValue>;
};

export type PutSlotResult = {
	slot: SlotView;
	applied_time_changes: string[];
	warnings: string[];
};

export type CreateSlotIn = {
	instance: string;
	render_time: string;
	upload_time: string;
	auto_approve: boolean;
};

export type DeleteSlotResult = {
	instance: string;
	manifest_wiped: boolean;
	orphan_post_ids: string[];
	warnings: string[];
};

export async function apiDelete<T>(path: string): Promise<T> {
	const csrfToken = await _getCsrfToken();
	const r = await fetch(path, { method: 'DELETE', headers: { 'X-CSRF-Token': csrfToken } });
	if (!r.ok) {
		if (r.status === 403) _invalidateCsrfToken();
		let msg = `${r.status}`;
		try {
			const j = await r.json();
			if (j?.detail) msg = j.detail;
		} catch {
			// non-JSON body
		}
		throw new Error(msg);
	}
	return (await r.json()) as T;
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
	const csrfToken = await _getCsrfToken();
	const r = await fetch(path, {
		method: 'POST',
		headers: body
			? { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken }
			: { 'X-CSRF-Token': csrfToken },
		body: body ? JSON.stringify(body) : undefined
	});
	if (!r.ok) {
		if (r.status === 403) _invalidateCsrfToken();
		let msg = `${r.status}`;
		try {
			const j = await r.json();
			if (j?.detail) msg = j.detail;
		} catch {
			// non-JSON body — keep status code
		}
		throw new Error(msg);
	}
	return (await r.json()) as T;
}
