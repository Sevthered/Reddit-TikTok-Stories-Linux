<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import * as Tabs from '$lib/components/ui/tabs';
	import { Button } from '$lib/components/ui/button';
	import { Skeleton } from '$lib/components/ui/skeleton';
	import { Switch } from '$lib/components/ui/switch';
	import { ScrollArea } from '$lib/components/ui/scroll-area';
	import JobSheet from '$lib/JobSheet.svelte';
	import { Play, Rocket, Radar, XSquare } from '@lucide/svelte';
	import {
		apiGet,
		apiPost,
		type StatusOut,
		type HealthOut,
		type JobOut
	} from '$lib/api';

	// ---- Overview state -------------------------------------------------

	let status = $state<StatusOut | null>(null);
	let health = $state<HealthOut | null>(null);
	let err = $state<string | null>(null);

	// ---- Action state ---------------------------------------------------

	let sheetOpen = $state(false);
	let currentJobId = $state<string | null>(null);
	let currentKind = $state('');
	let starting = $state<{ render: boolean; upload: boolean; confirm: boolean }>({
		render: false,
		upload: false,
		confirm: false
	});
	let running = $state<{ render: boolean; upload: boolean; confirm: boolean }>({
		render: false,
		upload: false,
		confirm: false
	});

	let renderLimit = $state(1);
	let renderDry = $state(false);
	let uploadForce = $state(false);
	let uploadDry = $state(false);
	let confirmForce = $state(false);

	// ---- Log console state ---------------------------------------------

	type LogChannel = 'upload_worker' | 'bot' | 'confirm_live';
	let logChan = $state<LogChannel>('upload_worker');
	let logLines = $state<Record<LogChannel, string[]>>({
		upload_worker: [],
		bot: [],
		confirm_live: []
	});
	let logConnected = $state(false);
	let sse: EventSource | null = null;
	let autoscroll = $state(true);
	let logViewport: HTMLDivElement | null = $state(null);

	function connectLogStream(chan: LogChannel) {
		if (sse) {
			sse.close();
			sse = null;
		}
		logConnected = false;
		const es = new EventSource(`/api/logs/${chan}/stream`);
		es.addEventListener('line', (ev) => {
			const payload = (ev as MessageEvent).data as string;
			const arr = payload.split('\n').filter(Boolean);
			// Cap channel scrollback to keep DOM small.
			const combined = [...logLines[chan], ...arr];
			const capped = combined.length > 500 ? combined.slice(-500) : combined;
			logLines = { ...logLines, [chan]: capped };
			if (autoscroll) queueMicrotask(scrollLogToEnd);
		});
		es.addEventListener('ping', () => {
			logConnected = true;
		});
		es.onopen = () => {
			logConnected = true;
		};
		es.onerror = () => {
			logConnected = false;
		};
		sse = es;
	}

	function scrollLogToEnd() {
		if (!logViewport) return;
		// ScrollArea renders an inner viewport div; select it via data attr.
		const vp = logViewport.querySelector<HTMLDivElement>(
			'[data-slot="scroll-area-viewport"]'
		);
		if (vp) vp.scrollTop = vp.scrollHeight;
	}

	function switchChannel(next: string) {
		if (next !== 'upload_worker' && next !== 'bot' && next !== 'confirm_live') return;
		logChan = next;
		connectLogStream(next);
	}

	// ---- Data loaders --------------------------------------------------

	async function loadOverview() {
		try {
			const [s, h] = await Promise.all([
				apiGet<StatusOut>('/api/status'),
				apiGet<HealthOut>('/api/health')
			]);
			status = s;
			health = h;
			err = null;
		} catch (e) {
			err = (e as Error).message;
		}
	}

	async function loadRunningJobs() {
		try {
			const jobs = await apiGet<JobOut[]>('/api/jobs');
			running = {
				render: jobs.some((j) => j.kind === 'render' && j.running),
				upload: jobs.some((j) => j.kind === 'upload' && j.running),
				confirm: jobs.some((j) => j.kind === 'confirm' && j.running)
			};
		} catch {
			// best-effort — the log console is the source of truth for live state anyway
		}
	}

	async function startJob(kind: 'render' | 'upload' | 'confirm', body: object) {
		starting = { ...starting, [kind]: true };
		try {
			const job = await apiPost<JobOut>(`/api/jobs/${kind}`, body);
			currentJobId = job.id;
			currentKind = kind;
			sheetOpen = true;
			toast.success(`${kind[0].toUpperCase()}${kind.slice(1)} started`, {
				description: `job ${job.id}`
			});
			running = { ...running, [kind]: true };
		} catch (e) {
			toast.error(`${kind} failed to start`, { description: (e as Error).message });
		} finally {
			starting = { ...starting, [kind]: false };
		}
	}

	onMount(() => {
		loadOverview();
		loadRunningJobs();
		const overviewId = setInterval(loadOverview, 10_000);
		const jobsId = setInterval(loadRunningJobs, 5_000);
		connectLogStream(logChan);
		return () => {
			clearInterval(overviewId);
			clearInterval(jobsId);
		};
	});

	onDestroy(() => {
		if (sse) sse.close();
	});

	// ---- Formatters ----------------------------------------------------

	function sessionDot(days: number | null): string {
		if (days === null) return 'bg-muted-foreground';
		if (days < 2) return 'bg-destructive';
		if (days < 7) return 'bg-warning';
		return 'bg-success';
	}

	function sessionLabel(days: number | null): string {
		if (days === null) return '—';
		if (days < 0) return 'expired';
		return `${Math.floor(days)} days`;
	}
</script>

<div class="space-y-8">
	<!-- ============================ Header ============================ -->
	<div class="flex items-baseline justify-between border-b border-border/60 pb-4">
		<div>
			<h1 class="text-[20px] font-semibold tracking-tight">Overview</h1>
			<p class="eyebrow mt-1">Reddit → TikTok pipeline · Europe/Madrid</p>
		</div>
		{#if health && (!health.db_reachable || !health.config_loaded)}
			<div class="flex items-center gap-2 text-[12px]">
				{#if !health.db_reachable}
					<span class="flex items-center gap-1.5">
						<span class="dot bg-destructive"></span>
						<span class="text-destructive">DB unreachable</span>
					</span>
				{/if}
				{#if !health.config_loaded}
					<span class="flex items-center gap-1.5">
						<span class="dot bg-destructive"></span>
						<span class="text-destructive">Config error</span>
					</span>
				{/if}
			</div>
		{/if}
	</div>

	{#if err}
		<Card.Root class="border-destructive/60 bg-destructive/5">
			<Card.Header class="py-4">
				<Card.Title class="text-[13px] text-destructive font-medium">API error</Card.Title>
				<Card.Description class="font-mono text-[12px]">{err}</Card.Description>
			</Card.Header>
		</Card.Root>
	{/if}

	<!-- ============================ KPI tiles ========================= -->
	<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
		<!-- Posts today -->
		<Card.Root>
			<Card.Header class="pb-3">
				<span class="eyebrow">Posts today</span>
				<div class="pt-1.5">
					{#if status}
						<span class="font-mono text-[24px] font-medium tnum text-foreground">
							{status.posts_today}
						</span>
					{:else}
						<Skeleton class="h-6 w-10" />
					{/if}
				</div>
			</Card.Header>
		</Card.Root>

		<!-- Pending renders -->
		<Card.Root>
			<Card.Header class="pb-3">
				<span class="eyebrow">Pending renders</span>
				<div class="pt-1.5 flex items-center gap-2">
					{#if status}
						<span class="font-mono text-[24px] font-medium tnum text-foreground">
							{status.pending_count}
						</span>
						{#if status.pending_count > 0}
							<span class="dot bg-info" aria-hidden="true"></span>
						{/if}
					{:else}
						<Skeleton class="h-6 w-10" />
					{/if}
				</div>
			</Card.Header>
		</Card.Root>

		<!-- Under review -->
		<Card.Root>
			<Card.Header class="pb-3">
				<span class="eyebrow">Under review</span>
				<div class="pt-1.5 flex items-center gap-2">
					{#if status}
						<span class="font-mono text-[24px] font-medium tnum text-foreground">
							{status.under_review_count}
						</span>
						{#if status.under_review_count > 0}
							<span class="dot bg-warning" aria-hidden="true"></span>
						{/if}
					{:else}
						<Skeleton class="h-6 w-10" />
					{/if}
				</div>
			</Card.Header>
		</Card.Root>

		<!-- Session -->
		<Card.Root>
			<Card.Header class="pb-3">
				<span class="eyebrow">Session</span>
				<div class="pt-1.5 flex items-center gap-2">
					{#if status}
						<span
							class="dot {sessionDot(status.sessionid_days_remaining !== null
								? Math.floor(status.sessionid_days_remaining)
								: null)}"
							aria-hidden="true"
						></span>
						<span class="font-mono text-[15px] tnum text-foreground">
							{sessionLabel(
								status.sessionid_days_remaining !== null
									? Math.floor(status.sessionid_days_remaining)
									: null
							)}
						</span>
					{:else}
						<Skeleton class="h-5 w-16" />
					{/if}
				</div>
			</Card.Header>
		</Card.Root>
	</div>

	<!-- ============================ Actions =========================== -->
	<section class="space-y-3">
		<div class="flex items-baseline justify-between">
			<h2 class="eyebrow">Actions</h2>
			<span class="text-[11px] text-muted-foreground">
				One instance per kind. Running actions show a live indicator.
			</span>
		</div>

		<div class="grid grid-cols-1 md:grid-cols-3 gap-3">
			<!-- Render -->
			<Card.Root class={running.render ? 'rail-live' : ''}>
				<Card.Header class="pb-3">
					<div class="flex items-start justify-between">
						<div>
							<div class="flex items-center gap-2">
								<Play size={14} strokeWidth={1.75} class="text-muted-foreground" />
								<span class="text-[13px] font-medium">Render</span>
							</div>
							<p class="eyebrow mt-1">python main.py</p>
						</div>
						{#if running.render}
							<span class="flex items-center gap-1.5 text-[11px]">
								<span class="dot dot--live" aria-hidden="true"></span>
								<span class="eyebrow" style="color: var(--live)">On air</span>
							</span>
						{/if}
					</div>
				</Card.Header>
				<Card.Content class="space-y-3 pt-0">
					<label class="flex items-center justify-between text-[12px]">
						<span class="text-muted-foreground">Limit</span>
						<input
							type="number"
							min="1"
							max="10"
							bind:value={renderLimit}
							class="w-16 rounded-md border border-input bg-background px-2 py-1 text-[12px] font-mono tnum text-right focus:outline-none focus:ring-2 focus:ring-ring"
						/>
					</label>
					<label class="flex items-center justify-between text-[12px]">
						<span class="text-muted-foreground">Dry run</span>
						<Switch bind:checked={renderDry} class="scale-90" />
					</label>
					<Button
						size="sm"
						class="w-full"
						disabled={starting.render || running.render}
						onclick={() => startJob('render', { limit: renderLimit, dry_run: renderDry })}
					>
						{running.render ? 'Running…' : 'Start render'}
					</Button>
				</Card.Content>
			</Card.Root>

			<!-- Upload -->
			<Card.Root class={running.upload ? 'rail-live' : ''}>
				<Card.Header class="pb-3">
					<div class="flex items-start justify-between">
						<div>
							<div class="flex items-center gap-2">
								<Rocket size={14} strokeWidth={1.75} class="text-muted-foreground" />
								<span class="text-[13px] font-medium">Upload worker</span>
							</div>
							<p class="eyebrow mt-1">python -m pipeline.upload_worker</p>
						</div>
						{#if running.upload}
							<span class="flex items-center gap-1.5 text-[11px]">
								<span class="dot dot--live" aria-hidden="true"></span>
								<span class="eyebrow" style="color: var(--live)">On air</span>
							</span>
						{/if}
					</div>
				</Card.Header>
				<Card.Content class="space-y-3 pt-0">
					<label class="flex items-center justify-between text-[12px]">
						<span class="text-muted-foreground">Force gates</span>
						<Switch bind:checked={uploadForce} class="scale-90" />
					</label>
					<label class="flex items-center justify-between text-[12px]">
						<span class="text-muted-foreground">Dry run</span>
						<Switch bind:checked={uploadDry} class="scale-90" />
					</label>
					<Button
						size="sm"
						class="w-full"
						disabled={starting.upload || running.upload}
						onclick={() => startJob('upload', { force: uploadForce, dry_run: uploadDry })}
					>
						{running.upload ? 'Running…' : 'Run upload'}
					</Button>
				</Card.Content>
			</Card.Root>

			<!-- Confirm -->
			<Card.Root class={running.confirm ? 'rail-live' : ''}>
				<Card.Header class="pb-3">
					<div class="flex items-start justify-between">
						<div>
							<div class="flex items-center gap-2">
								<Radar size={14} strokeWidth={1.75} class="text-muted-foreground" />
								<span class="text-[13px] font-medium">Confirm live</span>
							</div>
							<p class="eyebrow mt-1">python -m pipeline.confirm_live</p>
						</div>
						{#if running.confirm}
							<span class="flex items-center gap-1.5 text-[11px]">
								<span class="dot dot--live" aria-hidden="true"></span>
								<span class="eyebrow" style="color: var(--live)">On air</span>
							</span>
						{/if}
					</div>
				</Card.Header>
				<Card.Content class="space-y-3 pt-0">
					<label class="flex items-center justify-between text-[12px]">
						<span class="text-muted-foreground">Force</span>
						<Switch bind:checked={confirmForce} class="scale-90" />
					</label>
					<div class="h-[27px]"></div>
					<Button
						size="sm"
						class="w-full"
						disabled={starting.confirm || running.confirm}
						onclick={() => startJob('confirm', { force: confirmForce })}
					>
						{running.confirm ? 'Running…' : 'Run confirm'}
					</Button>
				</Card.Content>
			</Card.Root>
		</div>
	</section>

	<!-- ============================ Live console ====================== -->
	<!-- Signature (part 2): terminal-style mission control readout. -->
	<section class="space-y-3">
		<div class="flex items-baseline justify-between">
			<h2 class="eyebrow">Live console</h2>
			<span class="flex items-center gap-1.5 text-[11px] text-muted-foreground">
				{#if logConnected}
					<span class="dot dot--live" aria-hidden="true"></span>
					<span class="eyebrow" style="color: var(--live)">Streaming</span>
				{:else}
					<span class="dot bg-muted-foreground" aria-hidden="true"></span>
					<span class="eyebrow">Idle</span>
				{/if}
			</span>
		</div>

		<Card.Root class="overflow-hidden">
			<div class="border-b border-border/70 px-3 py-1.5 flex items-center justify-between gap-3">
				<Tabs.Root value={logChan} onValueChange={switchChannel}>
					<Tabs.List class="h-8 bg-transparent p-0 gap-1">
						<Tabs.Trigger value="upload_worker" class="text-[11px] px-2 h-7">
							upload_worker
						</Tabs.Trigger>
						<Tabs.Trigger value="bot" class="text-[11px] px-2 h-7">bot</Tabs.Trigger>
						<Tabs.Trigger value="confirm_live" class="text-[11px] px-2 h-7">
							confirm_live
						</Tabs.Trigger>
					</Tabs.List>
				</Tabs.Root>
				<label class="flex items-center gap-2 text-[11px] text-muted-foreground">
					<span class="eyebrow text-[10px]">Autoscroll</span>
					<Switch bind:checked={autoscroll} class="scale-75" />
				</label>
			</div>
			<div class="bg-[oklch(from_var(--card)_calc(l-0.02)_c_h)]" bind:this={logViewport}>
				<ScrollArea class="h-[320px]">
					<div class="px-3 py-3 font-mono text-[11.5px] leading-[1.55]">
						{#if logLines[logChan].length === 0}
							<div class="text-muted-foreground">
								No output yet. Logs will stream here.
							</div>
						{:else}
							{#each logLines[logChan] as line, i (i)}
								<div
									class="whitespace-pre-wrap {
										/\berror\b/i.test(line)
											? 'text-destructive'
											: /\bwarn(ing)?\b/i.test(line)
												? 'text-warning'
												: 'text-foreground/85'
									}"
								>{line}</div>
							{/each}
						{/if}
					</div>
				</ScrollArea>
			</div>
		</Card.Root>
	</section>

	<!-- ============================ Agents ============================ -->
	{#if status}
		<section class="space-y-3">
			<div class="flex items-baseline justify-between">
				<h2 class="eyebrow">Systemd units</h2>
				<span class="text-[11px] text-muted-foreground">
					self-managed webapp not shown as actionable
				</span>
			</div>
			<Card.Root>
				<div class="divide-y divide-border/60">
					{#each status.agents as a}
						<div class="flex items-center justify-between px-4 py-2.5 text-[12px]">
							<span class="font-mono truncate text-foreground/90">{a.label}</span>
							<span class="flex items-center gap-3 shrink-0">
								{#if a.pid}
									<span class="flex items-center gap-1.5">
										<span class="dot bg-success"></span>
										<span class="eyebrow">pid</span>
										<span class="font-mono tnum">{a.pid}</span>
									</span>
								{:else if a.loaded}
									<span class="flex items-center gap-1.5">
										<span class="dot bg-muted-foreground"></span>
										<span class="eyebrow">loaded</span>
									</span>
								{:else}
									<span class="flex items-center gap-1.5">
										<span class="dot bg-muted-foreground/50"></span>
										<span class="eyebrow">unloaded</span>
									</span>
								{/if}
								{#if a.last_exit_code !== null && a.last_exit_code !== 0}
									<span class="flex items-center gap-1.5">
										<XSquare size={12} strokeWidth={2} class="text-destructive" />
										<span class="text-destructive font-mono tnum">exit {a.last_exit_code}</span>
									</span>
								{/if}
							</span>
						</div>
					{/each}
				</div>
			</Card.Root>
		</section>
	{/if}
</div>

<JobSheet
	bind:open={sheetOpen}
	jobId={currentJobId}
	kind={currentKind}
	onOpenChange={(v) => (sheetOpen = v)}
/>
