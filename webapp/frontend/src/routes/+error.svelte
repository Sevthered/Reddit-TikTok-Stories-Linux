<script lang="ts">
	import { page } from '$app/state';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
</script>

<div class="max-w-lg mx-auto pt-16">
	<Card.Root class="border-destructive">
		<Card.Header>
			<Card.Title class="text-destructive">
				{page.status} — {page.error?.message ?? 'unknown error'}
			</Card.Title>
			<Card.Description>
				{#if page.status === 404}
					Route not found. Client-side routes: /, /queue, /config, /logs.
				{:else}
					Something broke rendering this page.
				{/if}
			</Card.Description>
		</Card.Header>
		<Card.Footer>
			<Button variant="outline" onclick={() => history.back()}>Back</Button>
			<Button onclick={() => (location.href = '/')}>Dashboard</Button>
		</Card.Footer>
	</Card.Root>
</div>
