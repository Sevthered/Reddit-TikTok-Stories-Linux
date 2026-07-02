<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import * as Alert from '$lib/components/ui/alert';
	import * as AlertDialog from '$lib/components/ui/alert-dialog';
	import { Button, buttonVariants } from '$lib/components/ui/button';
	import { Switch } from '$lib/components/ui/switch';
	import { Skeleton } from '$lib/components/ui/skeleton';
	import { Separator } from '$lib/components/ui/separator';
	import { AlertTriangle, RotateCcw, Save } from '@lucide/svelte';
	import {
		apiGet,
		apiPost,
		apiPut,
		type SlotEffective,
		type SlotView,
		type SlotsOut,
		type PutSlotResult,
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
		if (timeToMinutes(w.upload_time) - timeToMinutes(w.render_time) < 15)
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

					<!-- ===== Reset ===== -->
					<div class="flex items-center justify-between">
						<p class="eyebrow text-[10px] text-muted-foreground">
							Reset clears every override in DB and removes the timer drop-ins.
						</p>
						<AlertDialog.Root
							open={resetDialog === slot.instance}
							onOpenChange={(v) => (resetDialog = v ? slot.instance : null)}
						>
							<AlertDialog.Trigger
								class={buttonVariants({ variant: 'ghost', size: 'sm' })}
								disabled={busy}
							>
								<RotateCcw size={13} strokeWidth={1.75} class="mr-1" />
								Reset to defaults
							</AlertDialog.Trigger>
							<AlertDialog.Content>
								<AlertDialog.Header>
									<AlertDialog.Title>Reset slot {slot.instance}?</AlertDialog.Title>
									<AlertDialog.Description>
										Clears every override for this slot and removes both timer
										drop-ins. Fire times revert to the base
										<code class="font-mono">.timer</code> files
										({slot.defaults.render_time} render, {slot.defaults.upload_time} upload).
										Behavior + notifications revert to code defaults.
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
					</div>
				</Card.Content>
			</Card.Root>
		{/each}
	{/if}
</div>
