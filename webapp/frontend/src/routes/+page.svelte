<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Skeleton } from '$lib/components/ui/skeleton';
	import { Switch } from '$lib/components/ui/switch';
	import JobSheet from '$lib/JobSheet.svelte';
	import { apiGet, apiPost, type StatusOut, type HealthOut, type JobOut } from '$lib/api';

	let sheetOpen = $state(false);
	let currentJobId = $state<string | null>(null);
	let currentKind = $state('');

	let renderLimit = $state(1);
	let renderDry = $state(false);
	let confirmForce = $state(false);
	let starting = $state(false);
	let agentBusy = $state<Record<string, boolean>>({});

	async function agentAction(label: string, action: 'load' | 'unload' | 'kickstart') {
		agentBusy = { ...agentBusy, [label]: true };
		try {
			await apiPost(`/api/agents/${label}/${action}`);
			toast.success(`${label}: ${action} ok`);
			await load();
		} catch (e) {
			toast.error(`${label}: ${action} failed`, { description: (e as Error).message });
		} finally {
			agentBusy = { ...agentBusy, [label]: false };
		}
	}

	async function startJob(kind: 'render' | 'upload' | 'confirm', body: object) {
		starting = true;
		try {
			const job = await apiPost<JobOut>(`/api/jobs/${kind}`, body);
			currentJobId = job.id;
			currentKind = kind;
			sheetOpen = true;
			toast.success(`${kind} job ${job.id} started`);
		} catch (e) {
			toast.error(`${kind} start failed`, { description: (e as Error).message });
		} finally {
			starting = false;
		}
	}

	let status = $state<StatusOut | null>(null);
	let health = $state<HealthOut | null>(null);
	let err = $state<string | null>(null);

	async function load() {
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

	onMount(() => {
		load();
		const id = setInterval(load, 10_000);
		return () => clearInterval(id);
	});

	function daysBadge(days: number | null) {
		if (days === null) return { label: 'unknown', variant: 'secondary' as const };
		if (days <= 0) return { label: `${days}d`, variant: 'destructive' as const };
		if (days <= 3) return { label: `${days}d`, variant: 'destructive' as const };
		if (days <= 7) return { label: `${days}d`, variant: 'secondary' as const };
		return { label: `${days}d`, variant: 'default' as const };
	}
</script>

<div class="space-y-6">
	<div class="flex items-baseline justify-between">
		<h1 class="text-2xl font-semibold tracking-tight">Dashboard</h1>
		{#if health}
			<span class="text-xs text-muted-foreground">
				Madrid offset: {health.madrid_offset_hours}h
				{#if !health.db_reachable}<Badge variant="destructive" class="ml-2">db down</Badge>{/if}
				{#if !health.config_loaded}<Badge variant="destructive" class="ml-2">config down</Badge>{/if}
			</span>
		{/if}
	</div>

	{#if err}
		<Card.Root class="border-destructive">
			<Card.Header>
				<Card.Title class="text-destructive">API error</Card.Title>
				<Card.Description>{err}</Card.Description>
			</Card.Header>
		</Card.Root>
	{/if}

	<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
		<Card.Root>
			<Card.Header>
				<Card.Description>Posts today (Madrid)</Card.Description>
				<Card.Title class="text-3xl">
					{#if status}{status.posts_today}{:else}<Skeleton class="h-9 w-12" />{/if}
				</Card.Title>
			</Card.Header>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Description>Pending renders</Card.Description>
				<Card.Title class="text-3xl">
					{#if status}{status.pending_count}{:else}<Skeleton class="h-9 w-12" />{/if}
				</Card.Title>
			</Card.Header>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Description>Under review</Card.Description>
				<Card.Title class="text-3xl">
					{#if status}{status.under_review_count}{:else}<Skeleton class="h-9 w-12" />{/if}
				</Card.Title>
			</Card.Header>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Description>Uploads</Card.Description>
				<Card.Title class="text-lg">
					{#if status}
						<Badge variant={status.uploads_enabled ? 'default' : 'secondary'}>
							{status.uploads_enabled ? 'enabled' : 'disabled'}
						</Badge>
					{:else}
						<Skeleton class="h-6 w-20" />
					{/if}
				</Card.Title>
			</Card.Header>
		</Card.Root>
	</div>

	<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
		<Card.Root>
			<Card.Header>
				<Card.Title>TikTok session</Card.Title>
				<Card.Description>sessionid cookie lifetime</Card.Description>
			</Card.Header>
			<Card.Content>
				{#if status}
					{@const b = daysBadge(status.sessionid_days_remaining !== null ? Math.floor(status.sessionid_days_remaining) : null)}
					<Badge variant={b.variant}>{b.label}</Badge>
					<div class="text-xs text-muted-foreground pt-2">
						Last upload: {status.last_uploaded_at ?? 'never'}
					</div>
				{:else}
					<Skeleton class="h-6 w-24" />
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title>LaunchAgents</Card.Title>
				<Card.Description>tiktok-webapp is self-managed (kickstart via CLI).</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-2">
				{#if status}
					{#each status.agents as a}
						{@const self = a.label === 'com.sebastian.tiktok-webapp'}
						<div class="flex items-center justify-between text-sm gap-3">
							<span class="font-mono truncate">{a.label}</span>
							<span class="flex items-center gap-2 shrink-0">
								{#if a.pid}
									<Badge variant="default">pid {a.pid}</Badge>
								{:else if a.loaded}
									<Badge variant="secondary">loaded</Badge>
								{:else}
									<Badge variant="outline">unloaded</Badge>
								{/if}
								{#if a.last_exit_code !== null && a.last_exit_code !== 0}
									<Badge variant="destructive">exit {a.last_exit_code}</Badge>
								{/if}
									{#if !self}
										{#if a.loaded}
											<Button
												size="sm"
												variant="outline"
												disabled={agentBusy[a.label]}
												onclick={() => agentAction(a.label, 'kickstart')}
											>
												Restart
											</Button>
											<Button
												size="sm"
												variant="destructive"
												disabled={agentBusy[a.label]}
												onclick={() => agentAction(a.label, 'unload')}
											>
												Stop
											</Button>
										{:else}
											<Button
												size="sm"
												disabled={agentBusy[a.label]}
												onclick={() => agentAction(a.label, 'load')}
											>
												Start
											</Button>
										{/if}
									{/if}
							</span>
						</div>
					{/each}
				{:else}
					<Skeleton class="h-4 w-full" />
					<Skeleton class="h-4 w-full" />
					<Skeleton class="h-4 w-full" />
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
		<Card.Root>
			<Card.Header>
				<Card.Title>Render</Card.Title>
				<Card.Description>python main.py</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3">
				<label class="flex items-center gap-3 text-sm">
					<span>limit</span>
					<input
						type="number"
						min="1"
						max="10"
						bind:value={renderLimit}
						class="w-20 rounded border bg-background px-2 py-1 text-sm"
					/>
				</label>
				<label class="flex items-center justify-between text-sm">
					<span>dry-run</span>
					<Switch bind:checked={renderDry} />
				</label>
				<Button
					class="w-full"
					disabled={starting}
					onclick={() => startJob('render', { limit: renderLimit, dry_run: renderDry })}
				>
					Start render
				</Button>
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title>Confirm live</Card.Title>
				<Card.Description>python -m pipeline.confirm_live</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3">
				<label class="flex items-center justify-between text-sm">
					<span>force</span>
					<Switch bind:checked={confirmForce} />
				</label>
				<Button
					class="w-full"
					disabled={starting}
					onclick={() => startJob('confirm', { force: confirmForce })}
				>
					Start confirm
				</Button>
			</Card.Content>
		</Card.Root>
	</div>
</div>

<JobSheet
	bind:open={sheetOpen}
	jobId={currentJobId}
	kind={currentKind}
	onOpenChange={(v) => (sheetOpen = v)}
/>
