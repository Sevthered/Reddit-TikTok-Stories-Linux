<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import * as Alert from '$lib/components/ui/alert';
	import * as AlertDialog from '$lib/components/ui/alert-dialog';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button, buttonVariants } from '$lib/components/ui/button';
	import { Switch } from '$lib/components/ui/switch';
	import { Skeleton } from '$lib/components/ui/skeleton';
	import { Separator } from '$lib/components/ui/separator';
	import { AlertTriangle, Plus, RotateCcw, Save, Trash2 } from '@lucide/svelte';
	import {
		apiGet,
		apiPost,
		apiPut,
		apiDelete,
		type SlotEffective,
		type SlotView,
		type SlotsOut,
		type PutSlotResult,
		type DeleteSlotResult,
		type SlotOverrideValue
	} from '$lib/api';

	// ---- Data + working state ------------------------------------------

	let loading = $state(true);
	let helperAvailable = $state(false);
	let slots = $state<SlotView[]>([]);
	// Working copy for edits, keyed by instance. Starts as a deep-ish copy
	// of effective so unsaved edits diff against the last-known server view.
	let working = $state<Record<string, SlotEffective>>({});
	let saving = $state<Record<string, boolean>>({});
	let resetDialog = $state<string | null>(null);
	let deleteDialog = $state<string | null>(null);

	// ---- Add slot dialog ----------------------------------------------

	let addOpen = $state(false);
	let addRender = $state('12:00');
	let addUpload = $state('12:30');
	let addAutoApprove = $state(false);
	let addInstance = $state('1230');
	let addSubmitting = $state(false);

	function timeToMinutesSafe(hhmm: string): number {
		if (!/^\d{2}:\d{2}$/.test(hhmm)) return NaN;
		const [h, m] = hhmm.split(':');
		return parseInt(h) * 60 + parseInt(m);
	}

	function instanceFromUpload(hhmm: string): string {
		if (!/^\d{2}:\d{2}$/.test(hhmm)) return '';
		return hhmm.replace(':', '');
	}

	// Re-derive the instance name every time the operator touches
	// upload time, but let them override it manually before submit.
	let addInstanceUserEdited = $state(false);
	function onAddUploadChange(v: string) {
		addUpload = v;
		if (!addInstanceUserEdited) {
			addInstance = instanceFromUpload(v);
		}
	}
	function onAddInstanceChange(v: string) {
		addInstance = v.replace(/\D/g, '').slice(0, 4);
		addInstanceUserEdited = true;
	}

	function addOrderingError(): string | null {
		if (!/^\d{2}:\d{2}$/.test(addRender) || !/^\d{2}:\d{2}$/.test(addUpload))
			return 'Times must be HH:MM.';
		const r = timeToMinutesSafe(addRender);
		let u = timeToMinutesSafe(addUpload);
		if (u <= r) u += 24 * 60;
		if (u - r < 15) return 'Upload must be at least 15 minutes after render.';
		return null;
	}

	function addInstanceError(): string | null {
		if (!/^\d{4}$/.test(addInstance)) return 'Instance must be 4 digits.';
		if (slots.some((s) => s.instance === addInstance))
			return `Slot ${addInstance} already exists.`;
		return null;
	}

	function openAddDialog() {
		addRender = '12:00';
		addUpload = '12:30';
		addAutoApprove = false;
		addInstance = '1230';
		addInstanceUserEdited = false;
		addOpen = true;
	}

	async function submitAdd() {
		const ordErr = addOrderingError();
		const instErr = addInstanceError();
		if (ordErr || instErr) {
			toast.error('Cannot add slot', { description: ordErr ?? instErr ?? '' });
			return;
		}
		addSubmitting = true;
		try {
			const res = await apiPost<PutSlotResult>('/api/schedule/slots', {
				instance: addInstance,
				render_time: addRender,
				upload_time: addUpload,
				auto_approve: addAutoApprove
			});
			slots = [...slots, res.slot].sort((a, b) => a.instance.localeCompare(b.instance));
			working = { ...working, [res.slot.instance]: { ...res.slot.effective } };
			toast.success(`Slot ${res.slot.instance} added`);
			addOpen = false;
		} catch (e) {
			toast.error('Add slot failed', { description: (e as Error).message });
		} finally {
			addSubmitting = false;
		}
	}

	async function deleteSlotAction(instance: string) {
		saving = { ...saving, [instance]: true };
		try {
			const res = await apiDelete<DeleteSlotResult>(
				`/api/schedule/slots/${instance}`
			);
			slots = slots.filter((s) => s.instance !== instance);
			const nextWorking = { ...working };
			delete nextWorking[instance];
			working = nextWorking;
			const orphanNote =
				res.orphan_post_ids.length > 0
					? ` — ${res.orphan_post_ids.length} orphan post${
							res.orphan_post_ids.length === 1 ? '' : 's'
						} flagged via Telegram`
					: '';
			toast.success(`Slot ${instance} deleted${orphanNote}`);
		} catch (e) {
			toast.error(`Slot ${instance} delete failed`, {
				description: (e as Error).message
			});
		} finally {
			saving = { ...saving, [instance]: false };
			deleteDialog = null;
		}
	}

	async function load() {
		loading = true;
		try {
			const out = await apiGet<SlotsOut>('/api/schedule/slots');
			slots = out.slots;
			helperAvailable = out.helper_available;
			working = Object.fromEntries(out.slots.map((s) => [s.instance, { ...s.effective }]));
		} catch (e) {
			toast.error('Failed to load schedule', { description: (e as Error).message });
		} finally {
			loading = false;
		}
	}

	onMount(load);

	// ---- Save / reset --------------------------------------------------

	// Fields to check for changes. Times get pushed to the root helper +
	// DB; bools go DB-only.
	const TIME_FIELDS = ['render_time', 'upload_time'] as const;
	const BOOL_FIELDS = [
		'render_enabled',
		'upload_enabled',
		'auto_approve',
		'notify_render_pre',
		'notify_render_crash',
		'notify_render_empty',
		'notify_upload_approval_card',
		'notify_upload_force_approve',
		'notify_upload_success',
		'notify_upload_failure',
		'notify_upload_gate_reject'
	] as const;
	const ALL_FIELDS = [...TIME_FIELDS, ...BOOL_FIELDS] as const;

	type SlotField = (typeof ALL_FIELDS)[number];

	function diffOverrides(
		slot: SlotView,
		w: SlotEffective
	): Record<string, SlotOverrideValue> {
		const out: Record<string, SlotOverrideValue> = {};
		for (const field of ALL_FIELDS) {
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const cur = (w as any)[field] as string | boolean;
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const eff = (slot.effective as any)[field] as string | boolean;
			if (cur !== eff) out[field] = cur;
		}
		return out;
	}

	function hasChanges(slot: SlotView): boolean {
		return Object.keys(diffOverrides(slot, working[slot.instance])).length > 0;
	}

	function isOverride(slot: SlotView, field: SlotField): boolean {
		return slot.overrides[field] !== undefined;
	}

	function timeToMinutes(hhmm: string): number {
		const [h, m] = hhmm.split(':');
		return parseInt(h) * 60 + parseInt(m);
	}

	function orderingError(w: SlotEffective): string | null {
		if (!/^\d{2}:\d{2}$/.test(w.render_time) || !/^\d{2}:\d{2}$/.test(w.upload_time))
			return null;
		const r = timeToMinutes(w.render_time);
		let u = timeToMinutes(w.upload_time);
		// Cross-midnight: upload at 00:00 after render at 23:30 is +30min,
		// not -1410min. If upload appears at-or-before render, treat it as
		// the next day.
		if (u <= r) u += 24 * 60;
		if (u - r < 15)
			return 'Upload must be at least 15 minutes after render.';
		return null;
	}

	async function save(slot: SlotView) {
		const w = working[slot.instance];
		if (!w) return;
		const ordErr = orderingError(w);
		if (ordErr) {
			toast.error('Cannot save', { description: ordErr });
			return;
		}
		const overrides = diffOverrides(slot, w);
		if (Object.keys(overrides).length === 0) return;
		saving = { ...saving, [slot.instance]: true };
		try {
			const res = await apiPut<PutSlotResult>(
				`/api/schedule/slots/${slot.instance}`,
				{ overrides }
			);
			slots = slots.map((s) => (s.instance === slot.instance ? res.slot : s));
			working = { ...working, [slot.instance]: { ...res.slot.effective } };
			toast.success(`Slot ${slot.instance} saved`, {
				description: res.applied_time_changes.length
					? `Applied: ${res.applied_time_changes.join(', ')}`
					: 'Behavior toggles updated'
			});
		} catch (e) {
			toast.error(`Slot ${slot.instance} save failed`, { description: (e as Error).message });
		} finally {
			saving = { ...saving, [slot.instance]: false };
		}
	}

	function discard(slot: SlotView) {
		working = { ...working, [slot.instance]: { ...slot.effective } };
	}

	async function resetSlot(instance: string) {
		saving = { ...saving, [instance]: true };
		try {
			const res = await apiPost<PutSlotResult>(
				`/api/schedule/slots/${instance}/reset`
			);
			slots = slots.map((s) => (s.instance === instance ? res.slot : s));
			working = { ...working, [instance]: { ...res.slot.effective } };
			toast.success(`Slot ${instance} reset to defaults`);
		} catch (e) {
			toast.error(`Slot ${instance} reset failed`, { description: (e as Error).message });
		} finally {
			saving = { ...saving, [instance]: false };
			resetDialog = null;
		}
	}

	// ---- Notification groups (for render + upload cards) ----------------

	const RENDER_NOTIFS: { key: SlotField; label: string; desc: string }[] = [
		{
			key: 'notify_render_pre',
			label: 'Pre-render notice',
			desc: '"Rendering for HH:00 slot" sent 30 min before publish.'
		},
		{
			key: 'notify_render_crash',
			label: 'Render crash',
			desc: 'main.py exit != 0 after all retries.'
		},
		{
			key: 'notify_render_empty',
			label: 'Scrape empty',
			desc: 'No candidates found or all filtered. Off by default.'
		}
	];

	const UPLOAD_NOTIFS: { key: SlotField; label: string; desc: string }[] = [
		{
			key: 'notify_upload_approval_card',
			label: 'Approval card',
			desc: 'Interactive Approve / Reject card. No-op under auto-approve.'
		},
		{
			key: 'notify_upload_force_approve',
			label: 'Force approve',
			desc: 'Posted without approval after no reply at publish hour.'
		},
		{
			key: 'notify_upload_success',
			label: 'Success',
			desc: 'Confirmation that the video was posted.'
		},
		{
			key: 'notify_upload_failure',
			label: 'Failure',
			desc: 'Upload worker error (transient or terminal).'
		},
		{
			key: 'notify_upload_gate_reject',
			label: 'Gate rejected',
			desc: 'Upload gate refused (window / spacing / PAUSE). Off by default; opt-in.'
		}
	];
</script>

<div class="space-y-8">
	<!-- ============================ Header ============================ -->
	<div class="flex items-baseline justify-between border-b border-border/60 pb-4">
		<div>
			<h1 class="text-[20px] font-semibold tracking-tight">Schedule</h1>
			<p class="eyebrow mt-1">
				Per-slot render / upload times, notifications, auto-approve · Europe/Madrid
			</p>
		</div>
		<Button
			size="sm"
			onclick={openAddDialog}
			disabled={loading || !helperAvailable}
			title={!helperAvailable ? 'Requires root helper' : ''}
		>
			<Plus size={13} strokeWidth={1.75} class="mr-1" />
			Add slot
		</Button>
	</div>

	{#if !loading && !helperAvailable}
		<Alert.Root class="border-warning/50 bg-warning/5">
			<AlertTriangle class="text-warning" size={16} strokeWidth={1.75} />
			<Alert.Title class="text-[13px]">Time editing disabled</Alert.Title>
			<Alert.Description class="font-mono text-[11.5px]">
				/usr/local/sbin/tiktok-slot-time-write not found. Install via
				<code class="text-foreground">sudo bash scripts/install_systemd.sh install-helper</code>
				on the server. Behavior toggles still work.
			</Alert.Description>
		</Alert.Root>
	{/if}

	{#if loading}
		<div class="space-y-4">
			<Skeleton class="h-64 w-full" />
			<Skeleton class="h-64 w-full" />
		</div>
	{:else if slots.length === 0}
		<Alert.Root class="border-destructive/50 bg-destructive/5">
			<AlertTriangle class="text-destructive" size={16} strokeWidth={1.75} />
			<Alert.Title class="text-[13px]">No slots configured</Alert.Title>
			<Alert.Description class="text-[12px]">
				The pipeline will not fire automatically until at least one slot is added.
				Click <b>Add slot</b> above to schedule a render + upload cadence.
			</Alert.Description>
		</Alert.Root>
	{:else}
		{#each slots as slot (slot.instance)}
			{@const w = working[slot.instance]}
			{@const ordErr = orderingError(w)}
			{@const changed = hasChanges(slot)}
			{@const busy = saving[slot.instance]}

			<Card.Root>
				<!-- Slot header -->
				<Card.Header class="pb-4">
					<div class="flex items-start justify-between gap-4">
						<div class="flex items-center gap-3">
							<span
								class="font-mono text-[13px] px-2 py-0.5 rounded-md border border-border bg-muted/50 text-foreground/90 tnum"
							>
								{slot.instance}
							</span>
							<div>
								<div class="flex items-center gap-2">
									<span class="text-[13px] font-medium">
										{w.auto_approve ? 'Auto-approve slot' : 'Review-gated slot'}
									</span>
									<span
										class="dot {w.render_enabled && w.upload_enabled
											? 'bg-success'
											: !w.render_enabled && !w.upload_enabled
												? 'bg-muted-foreground'
												: 'bg-warning'}"
										aria-hidden="true"
									></span>
								</div>
								<p class="eyebrow mt-1">
									Publishes at
									<span class="font-mono text-foreground/80 ml-1 tnum">
										{w.upload_time}
									</span>
								</p>
							</div>
						</div>

						<div class="flex items-center gap-2">
							{#if changed}
								<span class="eyebrow text-[10px] px-2 py-1 rounded-md bg-warning/10 text-warning">
									Unsaved
								</span>
							{/if}
							<Button
								variant="ghost"
								size="sm"
								disabled={!changed || busy}
								onclick={() => discard(slot)}
							>
								Discard
							</Button>
							<Button
								size="sm"
								disabled={!changed || busy || !!ordErr}
								onclick={() => save(slot)}
							>
								<Save size={13} strokeWidth={1.75} class="mr-1" />
								{busy ? 'Saving…' : 'Save changes'}
							</Button>
						</div>
					</div>
				</Card.Header>

				<Separator />

				<Card.Content class="pt-6 space-y-6">
					<!-- ===== Times ===== -->
					<section class="grid grid-cols-1 md:grid-cols-2 gap-4">
						<div class="space-y-2">
							<div class="flex items-center justify-between">
								<span class="eyebrow">Render time</span>
								{#if isOverride(slot, 'render_time')}
									<span class="eyebrow text-[10px] text-primary">override</span>
								{/if}
							</div>
							<input
								type="time"
								bind:value={w.render_time}
								disabled={!helperAvailable || busy}
								class="w-40 rounded-md border border-input bg-background px-2.5 py-1.5 font-mono text-[13px] tnum focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
							/>
							<p class="eyebrow text-[10px] text-muted-foreground">
								Default {slot.defaults.render_time}
							</p>
						</div>
						<div class="space-y-2">
							<div class="flex items-center justify-between">
								<span class="eyebrow">Upload time (publish)</span>
								{#if isOverride(slot, 'upload_time')}
									<span class="eyebrow text-[10px] text-primary">override</span>
								{/if}
							</div>
							<input
								type="time"
								bind:value={w.upload_time}
								disabled={!helperAvailable || busy}
								class="w-40 rounded-md border border-input bg-background px-2.5 py-1.5 font-mono text-[13px] tnum focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
							/>
							<p class="eyebrow text-[10px] text-muted-foreground">
								Default {slot.defaults.upload_time}
							</p>
						</div>
					</section>

					{#if ordErr}
						<p class="text-[12px] text-destructive font-mono">{ordErr}</p>
					{/if}

					<Separator />

					<!-- ===== Behavior ===== -->
					<section class="space-y-4">
						<span class="eyebrow">Behavior</span>
						<div class="grid grid-cols-1 md:grid-cols-3 gap-4">
							<label class="flex items-center justify-between gap-3 text-[13px]">
								<span>
									<span class="block">Render enabled</span>
									<span class="eyebrow text-[10px] text-muted-foreground">
										Kill switch for this slot's render.
									</span>
								</span>
								<Switch bind:checked={w.render_enabled} disabled={busy} class="scale-90" />
							</label>
							<label class="flex items-center justify-between gap-3 text-[13px]">
								<span>
									<span class="block">Upload enabled</span>
									<span class="eyebrow text-[10px] text-muted-foreground">
										Kill switch for this slot's upload only.
									</span>
								</span>
								<Switch bind:checked={w.upload_enabled} disabled={busy} class="scale-90" />
							</label>
							<label class="flex items-center justify-between gap-3 text-[13px]">
								<span>
									<span class="block">Auto-approve render</span>
									<span class="eyebrow text-[10px] text-muted-foreground">
										Skip approval card and post at publish hour.
									</span>
								</span>
								<Switch bind:checked={w.auto_approve} disabled={busy} class="scale-90" />
							</label>
						</div>
					</section>

					<Separator />

					<!-- ===== Notifications ===== -->
					<section class="grid grid-cols-1 md:grid-cols-2 gap-6">
						<div class="space-y-3">
							<span class="eyebrow">Render notifications</span>
							{#each RENDER_NOTIFS as n (n.key)}
								<label
									class="flex items-start justify-between gap-3 text-[13px] pt-1"
								>
									<span>
										<span class="block">{n.label}</span>
										<span class="eyebrow text-[10px] text-muted-foreground">
											{n.desc}
										</span>
									</span>
									<Switch
										bind:checked={
											// eslint-disable-next-line @typescript-eslint/no-explicit-any
											(w as any)[n.key]
										}
										disabled={busy}
										class="scale-90 mt-1"
									/>
								</label>
							{/each}
						</div>
						<div class="space-y-3">
							<span class="eyebrow">Upload notifications</span>
							{#each UPLOAD_NOTIFS as n (n.key)}
								<label
									class="flex items-start justify-between gap-3 text-[13px] pt-1"
								>
									<span>
										<span class="block">{n.label}</span>
										<span class="eyebrow text-[10px] text-muted-foreground">
											{n.desc}
										</span>
									</span>
									<Switch
										bind:checked={
											// eslint-disable-next-line @typescript-eslint/no-explicit-any
											(w as any)[n.key]
										}
										disabled={busy}
										class="scale-90 mt-1"
									/>
								</label>
							{/each}
						</div>
					</section>

					<Separator />

					<!-- ===== Reset / Delete ===== -->
					<div class="flex items-center justify-between">
						<p class="eyebrow text-[10px] text-muted-foreground">
							Reset clears behavior + notification overrides. Delete removes the slot entirely.
						</p>
						<div class="flex items-center gap-1">
							<AlertDialog.Root
								open={resetDialog === slot.instance}
								onOpenChange={(v) => (resetDialog = v ? slot.instance : null)}
							>
								<AlertDialog.Trigger
									class={buttonVariants({ variant: 'ghost', size: 'sm' })}
									disabled={busy}
								>
									<RotateCcw size={13} strokeWidth={1.75} class="mr-1" />
									Reset overrides
								</AlertDialog.Trigger>
								<AlertDialog.Content>
									<AlertDialog.Header>
										<AlertDialog.Title>Reset slot {slot.instance}?</AlertDialog.Title>
										<AlertDialog.Description>
											Clears every behavior + notification override in the DB
											for this slot. Times ({slot.effective.render_time} render,
											{slot.effective.upload_time} upload) and
											auto-approve stay on the slot row. Delete the slot instead
											if you want to recreate it with different times.
										</AlertDialog.Description>
									</AlertDialog.Header>
									<AlertDialog.Footer>
										<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
										<AlertDialog.Action onclick={() => resetSlot(slot.instance)}>
											Reset
										</AlertDialog.Action>
									</AlertDialog.Footer>
								</AlertDialog.Content>
							</AlertDialog.Root>

							<AlertDialog.Root
								open={deleteDialog === slot.instance}
								onOpenChange={(v) => (deleteDialog = v ? slot.instance : null)}
							>
								<AlertDialog.Trigger
									class={buttonVariants({ variant: 'ghost', size: 'sm' })
										+ ' text-destructive hover:text-destructive'}
									disabled={busy || !helperAvailable}
									title={!helperAvailable ? 'Requires root helper' : ''}
								>
									<Trash2 size={13} strokeWidth={1.75} class="mr-1" />
									Delete slot
								</AlertDialog.Trigger>
								<AlertDialog.Content>
									<AlertDialog.Header>
										<AlertDialog.Title>Delete slot {slot.instance}?</AlertDialog.Title>
										<AlertDialog.Description>
											Stops + disables both timers, removes both drop-ins, wipes
											the DB row + every override. Any pending manifest is
											removed and any orphan pending posts are flagged via Telegram
											so you can approve / reject them via /queue.
											<span class="block mt-2 text-destructive">
												This is not reversible from the UI. Re-add the slot to restore it.
											</span>
										</AlertDialog.Description>
									</AlertDialog.Header>
									<AlertDialog.Footer>
										<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
										<AlertDialog.Action
											class="bg-destructive text-destructive-foreground hover:bg-destructive/90"
											onclick={() => deleteSlotAction(slot.instance)}
										>
											Delete
										</AlertDialog.Action>
									</AlertDialog.Footer>
								</AlertDialog.Content>
							</AlertDialog.Root>
						</div>
					</div>
				</Card.Content>
			</Card.Root>
		{/each}
	{/if}
</div>

<!-- ============================ Add slot dialog ==================== -->
<Dialog.Root bind:open={addOpen}>
	<Dialog.Content class="max-w-md">
		<Dialog.Header>
			<Dialog.Title>Add slot</Dialog.Title>
			<Dialog.Description>
				Creates a new render + upload pair on systemd. Instance name is
				auto-derived from upload time; edit it before submit if you want
				a different label.
			</Dialog.Description>
		</Dialog.Header>

		<div class="space-y-4 pt-2">
			<div class="grid grid-cols-2 gap-3">
				<div class="space-y-1.5">
					<span class="eyebrow">Render time</span>
					<input
						type="time"
						bind:value={addRender}
						class="w-full rounded-md border border-input bg-background px-2.5 py-1.5 font-mono text-[13px] tnum focus:outline-none focus:ring-2 focus:ring-ring"
					/>
				</div>
				<div class="space-y-1.5">
					<span class="eyebrow">Upload time (publish)</span>
					<input
						type="time"
						value={addUpload}
						oninput={(e) => onAddUploadChange((e.currentTarget as HTMLInputElement).value)}
						class="w-full rounded-md border border-input bg-background px-2.5 py-1.5 font-mono text-[13px] tnum focus:outline-none focus:ring-2 focus:ring-ring"
					/>
				</div>
			</div>

			{#if addOrderingError()}
				<p class="text-[12px] text-destructive font-mono">{addOrderingError()}</p>
			{/if}

			<div class="space-y-1.5">
				<span class="eyebrow">Instance name</span>
				<input
					type="text"
					inputmode="numeric"
					maxlength="4"
					value={addInstance}
					oninput={(e) => onAddInstanceChange((e.currentTarget as HTMLInputElement).value)}
					class="w-24 rounded-md border border-input bg-background px-2.5 py-1.5 font-mono text-[13px] tnum focus:outline-none focus:ring-2 focus:ring-ring"
				/>
				<p class="eyebrow text-[10px] text-muted-foreground">
					4 digits. Systemd unit identity — stays fixed after creation.
				</p>
				{#if addInstanceError()}
					<p class="text-[12px] text-destructive font-mono">{addInstanceError()}</p>
				{/if}
			</div>

			<label class="flex items-center justify-between gap-3 text-[13px]">
				<span>
					<span class="block">Auto-approve render</span>
					<span class="eyebrow text-[10px] text-muted-foreground">
						Skip approval card and post at publish hour. Interactive
						slots leave this off.
					</span>
				</span>
				<Switch bind:checked={addAutoApprove} class="scale-90" />
			</label>
		</div>

		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (addOpen = false)}>Cancel</Button>
			<Button
				disabled={addSubmitting || !!addOrderingError() || !!addInstanceError()}
				onclick={submitAdd}
			>
				{addSubmitting ? 'Creating…' : 'Create slot'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
