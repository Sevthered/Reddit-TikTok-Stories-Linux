<script lang="ts">
	import { onDestroy } from 'svelte';
	import * as Sheet from '$lib/components/ui/sheet';
	import { ScrollArea } from '$lib/components/ui/scroll-area';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { apiPost } from '$lib/api';
	import { toast } from 'svelte-sonner';

	type Props = {
		open: boolean;
		jobId: string | null;
		kind: string;
		onOpenChange: (v: boolean) => void;
	};

	let { open = $bindable(), jobId, kind, onOpenChange }: Props = $props();

	let lines = $state<string[]>([]);
	let status = $state<'streaming' | 'done' | 'error' | 'idle'>('idle');
	let exitCode = $state<string | null>(null);
	let source: EventSource | null = null;
	let scroller: HTMLDivElement | null = $state(null);

	function tearDown() {
		if (source) {
			source.close();
			source = null;
		}
	}

	function connect(id: string) {
		tearDown();
		lines = [];
		status = 'streaming';
		exitCode = null;
		source = new EventSource(`/api/jobs/${id}/stream`);
		source.addEventListener('line', (ev) => {
			lines = [...lines, (ev as MessageEvent).data];
			queueMicrotask(() => scroller?.scrollTo({ top: scroller.scrollHeight }));
		});
		source.addEventListener('end', (ev) => {
			exitCode = (ev as MessageEvent).data;
			status = 'done';
			tearDown();
		});
		source.addEventListener('ping', () => {});
		source.onerror = () => {
			status = 'error';
			tearDown();
		};
	}

	$effect(() => {
		if (open && jobId) {
			connect(jobId);
		} else if (!open) {
			tearDown();
		}
	});

	onDestroy(tearDown);

	async function cancel() {
		if (!jobId) return;
		try {
			await apiPost(`/api/jobs/${jobId}/cancel`);
			toast.success('cancel signal sent');
		} catch (e) {
			toast.error('cancel failed', { description: (e as Error).message });
		}
	}
</script>

<Sheet.Root {open} onOpenChange={(v) => onOpenChange(v)}>
	<Sheet.Content class="w-full sm:max-w-2xl flex flex-col">
		<Sheet.Header>
			<Sheet.Title>Job — {kind} <span class="font-mono text-xs">{jobId ?? ''}</span></Sheet.Title>
			<Sheet.Description>
				{#if status === 'streaming'}
					<Badge variant="secondary">streaming</Badge>
				{:else if status === 'done'}
					<Badge variant={exitCode === '0' ? 'default' : 'destructive'}>
						exit {exitCode}
					</Badge>
				{:else if status === 'error'}
					<Badge variant="destructive">stream error</Badge>
				{/if}
			</Sheet.Description>
		</Sheet.Header>

		<div class="flex-1 min-h-0 my-4">
			<ScrollArea class="h-[70vh] rounded border bg-black">
				<div class="p-3 font-mono text-xs text-green-300 whitespace-pre-wrap" bind:this={scroller}>
					{#each lines as ln, i}<div class="leading-tight">{ln}</div>{/each}
					{#if lines.length === 0}<div class="text-muted-foreground">waiting for output…</div>{/if}
				</div>
			</ScrollArea>
		</div>

		<Sheet.Footer>
			{#if status === 'streaming'}
				<Button variant="destructive" onclick={cancel}>Cancel job</Button>
			{/if}
			<Sheet.Close>
				<Button variant="outline">Close</Button>
			</Sheet.Close>
		</Sheet.Footer>
	</Sheet.Content>
</Sheet.Root>
