<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import * as Table from '$lib/components/ui/table';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { Switch } from '$lib/components/ui/switch';
	import { Skeleton } from '$lib/components/ui/skeleton';
	import JobSheet from '$lib/JobSheet.svelte';
	import { apiGet, apiPost, type RenderOut, type JobOut } from '$lib/api';

	let rows = $state<RenderOut[] | null>(null);
	let visibility = $state<'public' | 'only_me' | 'friends'>('only_me');
	let force = $state(false);
	let dryRun = $state(true);
	let aigc = $state(true);
	let starting = $state(false);

	let sheetOpen = $state(false);
	let currentJobId = $state<string | null>(null);
	let currentKind = $state('upload');
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	async function load() {
		try {
			rows = await apiGet<RenderOut[]>('/api/renders/approved');
		} catch (e) {
			toast.error('load failed', { description: (e as Error).message });
		}
	}

	async function startUpload() {
		starting = true;
		try {
			const job = await apiPost<JobOut>('/api/jobs/upload', {
				visibility,
				force,
				dry_run: dryRun,
				aigc
			});
			currentJobId = job.id;
			sheetOpen = true;
			toast.success(`upload job ${job.id} started`);
		} catch (e) {
			toast.error('start failed', { description: (e as Error).message });
		} finally {
			starting = false;
		}
	}

	onMount(() => {
		load();
		pollTimer = setInterval(load, 5000);
	});
	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});
</script>

<div class="space-y-6">
	<div class="flex items-baseline justify-between">
		<h1 class="text-2xl font-semibold tracking-tight">Upload</h1>
		<Button variant="outline" size="sm" onclick={load}>Refresh</Button>
	</div>

	<div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
		<Card.Root class="lg:col-span-1">
			<Card.Header>
				<Card.Title>Trigger upload</Card.Title>
				<Card.Description>
					Worker claims the oldest approved row. To publish a specific one,
					reject the newer ones first.
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-4">
				<div class="space-y-2">
					<span class="text-sm font-medium">Visibility</span>
					<div class="grid grid-cols-3 gap-2">
						{#each ['public', 'friends', 'only_me'] as v}
							<button
								type="button"
								onclick={() => (visibility = v as typeof visibility)}
								class="rounded border px-3 py-2 text-xs {visibility === v
									? 'bg-primary text-primary-foreground border-primary'
									: 'bg-background hover:bg-muted'}"
							>
								{v}
							</button>
						{/each}
					</div>
					<p class="text-xs text-muted-foreground">
						<span class="font-mono">only_me</span> = private, only you see the post.
						Recommended for the first real run.
					</p>
				</div>

				<div class="flex items-start justify-between gap-3">
					<div>
						<span class="text-sm font-medium">AIGC flag</span>
						<p class="text-xs text-muted-foreground">
							Marks the video as AI-generated (safer w.r.t. TikTok policy).
						</p>
					</div>
					<Switch bind:checked={aigc} />
				</div>

				<div class="flex items-start justify-between gap-3">
					<div>
						<span class="text-sm font-medium">Force</span>
						<p class="text-xs text-muted-foreground">
							Bypass window, spacing, and daily-cap gates. Still respects the
							pause switch. Leave OFF for scheduled runs.
						</p>
					</div>
					<Switch bind:checked={force} />
				</div>

				<div class="flex items-start justify-between gap-3">
					<div>
						<span class="text-sm font-medium">Dry run</span>
						<p class="text-xs text-muted-foreground">
							Claim the row, pretend to upload, release. No TikTok call.
						</p>
					</div>
					<Switch bind:checked={dryRun} />
				</div>

				<Button class="w-full" disabled={starting} onclick={startUpload}>
					Start upload
				</Button>
			</Card.Content>
		</Card.Root>

		<Card.Root class="lg:col-span-2">
			<Card.Header>
				<Card.Title>Approved queue</Card.Title>
				<Card.Description>
					Ready to upload, oldest first. Worker takes the top row.
				</Card.Description>
			</Card.Header>
			<Card.Content>
				{#if rows === null}
					<Skeleton class="h-32 w-full" />
				{:else if rows.length === 0}
					<p class="text-sm text-muted-foreground py-6 text-center">
						Nothing approved. Head to <a class="underline" href="/queue">/queue</a>
						and approve something first.
					</p>
				{:else}
					<Table.Root>
						<Table.Header>
							<Table.Row>
								<Table.Head>cover</Table.Head>
								<Table.Head>post_id</Table.Head>
								<Table.Head>subreddit</Table.Head>
								<Table.Head>title</Table.Head>
								<Table.Head class="text-right">attempts</Table.Head>
							</Table.Row>
						</Table.Header>
						<Table.Body>
							{#each rows as r, i (r.post_id)}
								<Table.Row class={i === 0 ? 'bg-primary/5' : ''}>
									<Table.Cell>
										<img
											src={`/api/cover/${r.post_id}`}
											alt=""
											loading="lazy"
											class="h-14 w-14 object-cover rounded border"
										/>
									</Table.Cell>
									<Table.Cell class="font-mono text-xs">
										{r.post_id}
										{#if i === 0}<Badge class="ml-2">next</Badge>{/if}
									</Table.Cell>
									<Table.Cell>r/{r.subreddit}</Table.Cell>
									<Table.Cell class="max-w-md truncate" title={r.title}>
										{r.title}
									</Table.Cell>
									<Table.Cell class="text-right">{r.upload_attempts}</Table.Cell>
								</Table.Row>
							{/each}
						</Table.Body>
					</Table.Root>
				{/if}
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
