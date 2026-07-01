<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { apiGet } from '$lib/api';

	type LogName = 'webapp' | 'bot' | 'upload_worker' | 'confirm_live';

	type LogTailOut = {
		name: LogName;
		stream: 'stdout' | 'stderr';
		lines: string[];
		truncated: boolean;
		bytes_read: number;
		file_size: number;
	};

	const NAMES: { key: LogName; label: string }[] = [
		{ key: 'webapp',        label: 'webapp' },
		{ key: 'bot',           label: 'bot' },
		{ key: 'upload_worker', label: 'upload' },
		{ key: 'confirm_live',  label: 'confirm' }
	];

	let selected = $state<LogName>('webapp');
	let lines = $state<string[]>([]);
	let following = $state(false);
	let es: EventSource | null = null;
	let viewport: HTMLDivElement | null = $state(null);
	let autoscroll = $state(true);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function loadTail(name: LogName) {
		loading = true;
		error = null;
		try {
			const out = await apiGet<LogTailOut>(`/api/logs/${name}/tail?lines=500`);
			lines = out.lines;
			if (autoscroll) scrollToBottom();
		} catch (e) {
			error = String(e);
			lines = [];
		} finally {
			loading = false;
		}
	}

	function scrollToBottom() {
		queueMicrotask(() => {
			if (viewport) viewport.scrollTop = viewport.scrollHeight;
		});
	}

	function attach(name: LogName) {
		detach();
		es = new EventSource(`/api/logs/${name}/stream`);
		es.addEventListener('line', (ev) => {
			const chunk = (ev as MessageEvent).data as string;
			for (const l of chunk.split('\n')) {
				lines.push(l);
			}
			// bound in-memory buffer
			if (lines.length > 5000) lines = lines.slice(-4000);
			lines = lines;
			if (autoscroll) scrollToBottom();
		});
		es.addEventListener('error', () => {
			following = false;
		});
		following = true;
	}

	function detach() {
		if (es) {
			es.close();
			es = null;
		}
		following = false;
	}

	function pick(name: LogName) {
		selected = name;
		lines = [];
		detach();
		loadTail(name);
	}

	function toggleFollow() {
		if (following) detach();
		else attach(selected);
	}

	function clearBuffer() {
		lines = [];
	}

	function copyAll() {
		navigator.clipboard.writeText(lines.join('\n'));
	}

	onMount(() => {
		loadTail(selected);
	});

	onDestroy(() => {
		detach();
	});
</script>

<div class="space-y-4">
	<div class="flex items-baseline justify-between">
		<h1 class="text-2xl font-semibold tracking-tight">Logs</h1>
		<div class="flex gap-2">
			<Button size="sm" variant="outline" onclick={() => loadTail(selected)} disabled={loading}>
				Refresh
			</Button>
			<Button size="sm" variant={following ? 'default' : 'outline'} onclick={toggleFollow}>
				{following ? 'Following' : 'Follow'}
			</Button>
			<Button size="sm" variant="outline" onclick={copyAll}>Copy</Button>
			<Button size="sm" variant="outline" onclick={clearBuffer}>Clear</Button>
		</div>
	</div>

	<div class="flex flex-wrap gap-2">
		{#each NAMES as n}
			<Button
				size="sm"
				variant={selected === n.key ? 'default' : 'outline'}
				onclick={() => pick(n.key)}
			>
				{n.label}
			</Button>
		{/each}
		<label class="ml-auto flex items-center gap-2 text-sm">
			<input type="checkbox" bind:checked={autoscroll} />
			<span>autoscroll</span>
		</label>
	</div>

	{#if error}
		<Card.Root>
			<Card.Content class="p-4 text-sm text-destructive">
				<Badge variant="destructive">error</Badge> {error}
			</Card.Content>
		</Card.Root>
	{/if}

	<Card.Root>
		<Card.Content class="p-0">
			<div
				bind:this={viewport}
				class="h-[70vh] overflow-auto rounded-md bg-black/90 p-3 font-mono text-xs leading-relaxed text-green-200"
			>
				{#if loading && lines.length === 0}
					<div class="text-muted-foreground">loading…</div>
				{:else if lines.length === 0}
					<div class="text-muted-foreground">no lines</div>
				{:else}
					{#each lines as ln}
						<div class="whitespace-pre-wrap break-all">{ln}</div>
					{/each}
				{/if}
			</div>
		</Card.Content>
	</Card.Root>
</div>
