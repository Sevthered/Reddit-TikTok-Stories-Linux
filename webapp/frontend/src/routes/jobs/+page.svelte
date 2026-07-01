<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Table from '$lib/components/ui/table';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Skeleton } from '$lib/components/ui/skeleton';
	import JobSheet from '$lib/JobSheet.svelte';
	import { apiGet, type JobOut } from '$lib/api';

	let jobs = $state<JobOut[] | null>(null);
	let sheetOpen = $state(false);
	let currentJobId = $state<string | null>(null);
	let currentKind = $state('');
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	async function load() {
		try {
			const rows = await apiGet<JobOut[]>('/api/jobs');
			rows.sort((a, b) => (a.started_at < b.started_at ? 1 : -1));
			jobs = rows;
		} catch {
			// non-fatal
		}
	}

	function attach(j: JobOut) {
		currentJobId = j.id;
		currentKind = j.kind;
		sheetOpen = true;
	}

	onMount(() => {
		load();
		pollTimer = setInterval(load, 3000);
	});

	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});

	function fmt(dt: string | null) {
		if (!dt) return '—';
		return dt.replace('T', ' ').replace(/\+.*$/, '');
	}
</script>

<div class="space-y-6">
	<div class="flex items-baseline justify-between">
		<h1 class="text-2xl font-semibold tracking-tight">Jobs</h1>
		<Button variant="outline" size="sm" onclick={load}>Refresh</Button>
	</div>

	<Card.Root>
		<Card.Header>
			<Card.Title>History</Card.Title>
			<Card.Description>
				Click a row to reopen its live log. Streaming reattaches to the
				subprocess if still running; otherwise replays the ring buffer.
			</Card.Description>
		</Card.Header>
		<Card.Content>
			{#if jobs === null}
				<Skeleton class="h-32 w-full" />
			{:else if jobs.length === 0}
				<p class="text-sm text-muted-foreground py-6 text-center">
					No jobs yet. Trigger one from the Dashboard.
				</p>
			{:else}
				<Table.Root>
					<Table.Header>
						<Table.Row>
							<Table.Head>id</Table.Head>
							<Table.Head>kind</Table.Head>
							<Table.Head>args</Table.Head>
							<Table.Head>started</Table.Head>
							<Table.Head>ended</Table.Head>
							<Table.Head>status</Table.Head>
							<Table.Head class="text-right">lines</Table.Head>
						</Table.Row>
					</Table.Header>
					<Table.Body>
						{#each jobs as j (j.id)}
							<Table.Row
								class="cursor-pointer hover:bg-muted/40"
								onclick={() => attach(j)}
							>
								<Table.Cell class="font-mono text-xs">{j.id}</Table.Cell>
								<Table.Cell>{j.kind}</Table.Cell>
								<Table.Cell class="font-mono text-xs max-w-xs truncate" title={j.args.join(' ')}>
									{j.args.join(' ')}
								</Table.Cell>
								<Table.Cell class="font-mono text-xs">{fmt(j.started_at)}</Table.Cell>
								<Table.Cell class="font-mono text-xs">{fmt(j.ended_at)}</Table.Cell>
								<Table.Cell>
									{#if j.running}
										<Badge>running</Badge>
									{:else if j.exit_code === 0}
										<Badge variant="default">exit 0</Badge>
									{:else}
										<Badge variant="destructive">exit {j.exit_code}</Badge>
									{/if}
								</Table.Cell>
								<Table.Cell class="text-right text-xs">{j.line_count}</Table.Cell>
							</Table.Row>
						{/each}
					</Table.Body>
				</Table.Root>
			{/if}
		</Card.Content>
	</Card.Root>
</div>

<JobSheet
	bind:open={sheetOpen}
	jobId={currentJobId}
	kind={currentKind}
	onOpenChange={(v) => (sheetOpen = v)}
/>
