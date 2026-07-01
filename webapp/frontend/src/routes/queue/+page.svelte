<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import * as Table from '$lib/components/ui/table';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Skeleton } from '$lib/components/ui/skeleton';
	import { apiGet, apiPost, type RenderOut } from '$lib/api';

	let previewId = $state<string | null>(null);
	let previewOpen = $state(false);

	let rows = $state<RenderOut[] | null>(null);
	let err = $state<string | null>(null);
	let busy = $state<Record<string, boolean>>({});

	async function load() {
		try {
			rows = await apiGet<RenderOut[]>('/api/renders/pending');
			err = null;
		} catch (e) {
			err = (e as Error).message;
		}
	}

	async function act(post_id: string, action: 'approve' | 'reject') {
		busy = { ...busy, [post_id]: true };
		try {
			await apiPost<RenderOut>(`/api/renders/${post_id}/${action}`);
			toast.success(`${action === 'approve' ? 'Approved' : 'Rejected'} ${post_id}`);
			await load();
		} catch (e) {
			toast.error(`${action} ${post_id} failed`, { description: (e as Error).message });
		} finally {
			busy = { ...busy, [post_id]: false };
		}
	}

	function confirmReject(post_id: string) {
		if (confirm(`Reject ${post_id}? Video + cover files will be deleted.`)) {
			act(post_id, 'reject');
		}
	}

	onMount(() => {
		load();
		const id = setInterval(load, 15_000);
		return () => clearInterval(id);
	});
</script>

<div class="space-y-6">
	<div class="flex items-baseline justify-between">
		<h1 class="text-2xl font-semibold tracking-tight">Review queue</h1>
		<Button variant="outline" size="sm" onclick={load}>Refresh</Button>
	</div>

	{#if err}
		<Card.Root class="border-destructive">
			<Card.Header>
				<Card.Title class="text-destructive">API error</Card.Title>
				<Card.Description>{err}</Card.Description>
			</Card.Header>
		</Card.Root>
	{/if}

	<Card.Root>
		<Card.Header>
			<Card.Title>Pending renders</Card.Title>
			<Card.Description>Rows awaiting human decision</Card.Description>
		</Card.Header>
		<Card.Content>
			{#if rows === null}
				<div class="space-y-2">
					<Skeleton class="h-8 w-full" />
					<Skeleton class="h-8 w-full" />
					<Skeleton class="h-8 w-full" />
				</div>
			{:else if rows.length === 0}
				<p class="text-sm text-muted-foreground py-6 text-center">
					Nothing pending. Run <code class="font-mono">python main.py --limit 1</code>.
				</p>
			{:else}
				<Table.Root>
					<Table.Header>
						<Table.Row>
							<Table.Head>cover</Table.Head>
							<Table.Head>post_id</Table.Head>
							<Table.Head>subreddit</Table.Head>
							<Table.Head>title</Table.Head>
							<Table.Head>status</Table.Head>
							<Table.Head class="text-right">actions</Table.Head>
						</Table.Row>
					</Table.Header>
					<Table.Body>
						{#each rows as r (r.post_id)}
							<Table.Row>
								<Table.Cell>
									<button
										type="button"
										aria-label={`Preview ${r.post_id}`}
										onclick={() => {
											previewId = r.post_id;
											previewOpen = true;
										}}
										class="block"
									>
										<img
											src={`/api/cover/${r.post_id}`}
											alt=""
											loading="lazy"
											class="h-16 w-16 object-cover rounded border cursor-pointer"
										/>
									</button>
								</Table.Cell>
								<Table.Cell class="font-mono text-xs">{r.post_id}</Table.Cell>
								<Table.Cell>r/{r.subreddit}</Table.Cell>
								<Table.Cell class="max-w-md truncate" title={r.title}>{r.title}</Table.Cell>
								<Table.Cell>
									<Badge variant="secondary">{r.upload_status}</Badge>
								</Table.Cell>
								<Table.Cell class="text-right space-x-2">
									<Button
										size="sm"
										disabled={busy[r.post_id]}
										onclick={() => act(r.post_id, 'approve')}
									>
										Approve
									</Button>
									<Button
										size="sm"
										variant="destructive"
										disabled={busy[r.post_id]}
										onclick={() => confirmReject(r.post_id)}
									>
										Reject
									</Button>
								</Table.Cell>
							</Table.Row>
						{/each}
					</Table.Body>
				</Table.Root>
			{/if}
		</Card.Content>
	</Card.Root>
</div>

<Dialog.Root bind:open={previewOpen}>
	<Dialog.Content class="max-w-lg">
		<Dialog.Header>
			<Dialog.Title>Preview — <span class="font-mono text-sm">{previewId ?? ''}</span></Dialog.Title>
			<Dialog.Description>Scrubs via HTTP Range 206.</Dialog.Description>
		</Dialog.Header>
		{#if previewId}
			<video
				controls
				preload="metadata"
				src={`/api/video/${previewId}`}
				class="w-full rounded border bg-black"
			>
				<track kind="captions" />
			</video>
		{/if}
	</Dialog.Content>
</Dialog.Root>
