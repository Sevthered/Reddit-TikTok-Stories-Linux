<script lang="ts">
	import '../app.css';
	import favicon from '$lib/assets/favicon.svg';
	import { onMount } from 'svelte';
	import { ModeWatcher, toggleMode, mode } from 'mode-watcher';
	import { page } from '$app/state';
	import { Toaster } from '$lib/components/ui/sonner';
	import {
		LayoutDashboard,
		ListChecks,
		Rocket,
		Play,
		Settings2,
		CalendarClock,
		Terminal,
		Sun,
		Moon
	} from '@lucide/svelte';
	import { apiGet, type StatusOut } from '$lib/api';

	let { children } = $props();

	type NavItem = { href: string; label: string; icon: typeof LayoutDashboard };
	const nav: NavItem[] = [
		{ href: '/', label: 'Dashboard', icon: LayoutDashboard },
		{ href: '/queue', label: 'Queue', icon: ListChecks },
		{ href: '/upload', label: 'Upload', icon: Rocket },
		{ href: '/jobs', label: 'Jobs', icon: Play },
		{ href: '/config', label: 'Config', icon: Settings2 },
		{ href: '/schedule', label: 'Schedule', icon: CalendarClock },
		{ href: '/logs', label: 'Logs', icon: Terminal }
	];

	function isActive(href: string): boolean {
		if (href === '/') return page.url.pathname === '/';
		return page.url.pathname.startsWith(href);
	}

	// ---- System status strip -------------------------------------------
	// Global telemetry above the content area. Polls every 10s. If the API
	// is unreachable, the strip renders in a degraded state (dashes + a
	// destructive dot) rather than hiding — the operator should always see
	// the machine's pulse from any page.
	let status = $state<StatusOut | null>(null);
	let statusErr = $state(false);

	async function loadStatus() {
		try {
			status = await apiGet<StatusOut>('/api/status');
			statusErr = false;
		} catch {
			statusErr = true;
		}
	}

	onMount(() => {
		loadStatus();
		const id = setInterval(loadStatus, 10_000);
		return () => clearInterval(id);
	});

	// ---- Derived state helpers ----------------------------------------
	function sessionDotClass(days: number | null): string {
		if (days === null) return 'bg-muted-foreground';
		if (days < 2) return 'bg-destructive';
		if (days < 7) return 'bg-warning';
		return 'bg-success';
	}

	function sessionLabel(days: number | null): string {
		if (days === null) return '—';
		if (days < 0) return 'expired';
		if (days < 1) return '<1d';
		return `${Math.floor(days)}d`;
	}

	function relTime(iso: string | null): string {
		if (!iso) return 'never';
		const t = Date.parse(iso);
		if (isNaN(t)) return iso;
		const secs = Math.max(0, (Date.now() - t) / 1000);
		if (secs < 60) return `${Math.floor(secs)}s ago`;
		if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
		if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
		return `${Math.floor(secs / 86400)}d ago`;
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>Control plane</title>
</svelte:head>

<ModeWatcher defaultMode="dark" />
<Toaster richColors position="top-right" />

<div class="app-shell flex min-h-screen">
	<!-- ============================ Sidebar ============================ -->
	<aside
		class="w-56 shrink-0 border-r border-sidebar-border bg-sidebar px-3 py-5 flex flex-col"
	>
		<!-- Identity block: monogram tile + name/role. No pulse, no gradient. -->
		<div class="flex items-center gap-2.5 px-2 pb-6">
			<div
				class="flex h-8 w-8 items-center justify-center rounded-md border border-sidebar-border bg-sidebar-accent text-sidebar-accent-foreground font-mono text-[13px] font-semibold"
			>
				RR
			</div>
			<div class="flex flex-col leading-tight">
				<span class="text-[13px] font-medium tracking-tight">Control plane</span>
				<span class="eyebrow">RRS pipeline</span>
			</div>
		</div>

		<!-- Nav — flat surface, small icons, no glow. -->
		<nav class="flex flex-col gap-0.5">
			{#each nav as item (item.href)}
				{@const active = isActive(item.href)}
				<a
					href={item.href}
					class="group flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors duration-150
						{active
						? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
						: 'text-sidebar-foreground/85 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground'}"
				>
					<item.icon
						class={active ? 'text-primary' : 'text-sidebar-foreground/60'}
						size={16}
						strokeWidth={1.75}
					/>
					<span>{item.label}</span>
				</a>
			{/each}
		</nav>

		<div class="mt-auto pt-4">
			<button
				type="button"
				onclick={toggleMode}
				class="flex w-full items-center justify-between rounded-md border border-sidebar-border/70 bg-sidebar-accent/40 px-2.5 py-1.5 text-[11px] text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground transition-colors duration-150"
			>
				<span class="flex items-center gap-2">
					{#if mode.current === 'dark'}
						<Moon size={13} strokeWidth={1.75} />
					{:else}
						<Sun size={13} strokeWidth={1.75} />
					{/if}
					{mode.current === 'dark' ? 'Dark' : 'Light'}
				</span>
				<span class="eyebrow text-[10px]">theme</span>
			</button>
		</div>
	</aside>

	<!-- ============================ Main =============================== -->
	<main class="flex-1 min-w-0 flex flex-col">
		<!-- Top status strip — the signature (part 1). Always visible,
		     compact, single row. Answers "is the machine healthy?" at a
		     glance. -->
		<div
			class="border-b border-border/70 bg-background/80 backdrop-blur px-6 py-2.5 flex items-center gap-6 text-[12px]"
		>
			<div class="flex items-center gap-2">
				<span
					class="dot {status
						? status.uploads_enabled ? 'bg-success' : 'bg-warning'
						: 'bg-muted-foreground'}"
					aria-hidden="true"
				></span>
				<span class="eyebrow text-[10px]">Uploads</span>
				<span class="text-foreground">
					{#if status}
						{status.uploads_enabled ? 'enabled' : 'paused'}
					{:else}
						—
					{/if}
				</span>
			</div>

			<div class="h-3.5 w-px bg-border"></div>

			<div class="flex items-center gap-2">
				<span class="eyebrow text-[10px]">Today</span>
				<span class="tnum font-mono text-foreground">
					{status ? status.posts_today : '—'}
				</span>
			</div>

			<div class="h-3.5 w-px bg-border"></div>

			<div class="flex items-center gap-2">
				<span
					class="dot {status
						? sessionDotClass(status.sessionid_days_remaining)
						: 'bg-muted-foreground'}"
					aria-hidden="true"
				></span>
				<span class="eyebrow text-[10px]">Session</span>
				<span class="tnum font-mono text-foreground">
					{status ? sessionLabel(status.sessionid_days_remaining) : '—'}
				</span>
			</div>

			<div class="h-3.5 w-px bg-border"></div>

			<div class="flex items-center gap-2 text-muted-foreground">
				<span class="eyebrow text-[10px]">Last upload</span>
				<span class="font-mono text-foreground/90">
					{status ? relTime(status.last_uploaded_at) : '—'}
				</span>
			</div>

			<div class="ml-auto flex items-center gap-3 text-muted-foreground">
				{#if statusErr}
					<span class="flex items-center gap-1.5">
						<span class="dot bg-destructive" aria-hidden="true"></span>
						<span class="eyebrow text-[10px] text-destructive">API unreachable</span>
					</span>
				{/if}
				{#if status}
					<span class="eyebrow text-[10px]">
						Madrid UTC+{status.madrid_offset_hours}
					</span>
				{/if}
			</div>
		</div>

		<!-- Content area — no ambient glow, no entrance choreography. -->
		<div class="flex-1 relative overflow-x-hidden">
			<div class="relative mx-auto max-w-7xl px-6 py-8">
				{@render children()}
			</div>
		</div>
	</main>
</div>

<style>
	.app-shell {
		background: var(--color-background);
	}
</style>
