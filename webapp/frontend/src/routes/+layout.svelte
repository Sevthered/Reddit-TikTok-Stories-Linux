<script lang="ts">
	import '../app.css';
	import favicon from '$lib/assets/favicon.svg';
	import { ModeWatcher, toggleMode, mode } from 'mode-watcher';
	import { page } from '$app/state';
	import { Toaster } from '$lib/components/ui/sonner';
	import { HugeiconsIcon } from '@hugeicons/svelte';
	import {
		DashboardSquare01Icon,
		Note01Icon,
		RocketIcon,
		FileScriptIcon,
		Settings01Icon,
		TerminalIcon,
		VideoReplayIcon,
		Sun02Icon,
		Moon02Icon
	} from '@hugeicons/core-free-icons';

	let { children } = $props();

	type NavItem = { href: string; label: string; icon: typeof DashboardSquare01Icon };
	const nav: NavItem[] = [
		{ href: '/', label: 'Dashboard', icon: DashboardSquare01Icon },
		{ href: '/queue', label: 'Queue', icon: Note01Icon },
		{ href: '/upload', label: 'Upload', icon: RocketIcon },
		{ href: '/jobs', label: 'Jobs', icon: VideoReplayIcon },
		{ href: '/config', label: 'Config', icon: Settings01Icon },
		{ href: '/logs', label: 'Logs', icon: TerminalIcon }
	];

	function isActive(href: string): boolean {
		if (href === '/') return page.url.pathname === '/';
		return page.url.pathname.startsWith(href);
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>TikTok Control Plane</title>
</svelte:head>

<ModeWatcher />
<Toaster richColors position="top-right" />

<div class="app-shell flex min-h-screen">
	<aside class="w-60 shrink-0 border-r border-sidebar-border/70 bg-sidebar/95 backdrop-blur px-3 py-5 flex flex-col gap-1">
		<div class="flex items-center gap-2 px-2 pb-6">
			<div
				class="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-fuchsia-500 via-rose-500 to-orange-400 text-white shadow-md shadow-rose-500/30"
			>
				<HugeiconsIcon icon={FileScriptIcon} size={18} strokeWidth={2} />
			</div>
			<div class="flex flex-col leading-tight">
				<span class="text-sm font-semibold tracking-tight">RRS Control</span>
				<span class="text-[10px] text-muted-foreground uppercase tracking-widest">plane</span>
			</div>
		</div>

		<nav class="flex flex-col gap-0.5">
			{#each nav as item (item.href)}
				{@const active = isActive(item.href)}
				<a
					href={item.href}
					class="group flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors
						{active
						? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
						: 'text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground'}"
				>
					<HugeiconsIcon
						icon={item.icon}
						size={18}
						strokeWidth={active ? 2.25 : 1.75}
						class={active ? 'text-primary' : ''}
					/>
					<span>{item.label}</span>
				</a>
			{/each}
		</nav>

		<div class="mt-auto pt-4">
			<button
				type="button"
				onclick={toggleMode}
				class="flex w-full items-center justify-between rounded-md border border-sidebar-border/70 bg-sidebar-accent/40 px-3 py-2 text-xs text-sidebar-foreground hover:bg-sidebar-accent"
			>
				<span class="flex items-center gap-2">
					<HugeiconsIcon icon={mode.current === 'dark' ? Moon02Icon : Sun02Icon} size={14} />
					{mode.current === 'dark' ? 'Dark' : 'Light'}
				</span>
				<span class="text-[10px] uppercase tracking-widest text-muted-foreground">toggle</span>
			</button>
		</div>
	</aside>

	<main class="flex-1 relative overflow-x-hidden">
		<div class="glow-bg absolute inset-0 pointer-events-none" aria-hidden="true"></div>
		<div class="relative mx-auto max-w-7xl px-6 py-8">
			{@render children()}
		</div>
	</main>
</div>

<style>
	.app-shell {
		background: var(--color-background);
	}
	.glow-bg {
		background:
			radial-gradient(ellipse 60% 40% at 15% 0%, oklch(0.72 0.19 12 / 0.10) 0%, transparent 60%),
			radial-gradient(ellipse 50% 45% at 90% 8%, oklch(0.75 0.16 300 / 0.10) 0%, transparent 60%);
		filter: blur(1px);
	}
	:global(.dark) .glow-bg {
		background:
			radial-gradient(ellipse 60% 40% at 15% 0%, oklch(0.55 0.24 12 / 0.15) 0%, transparent 60%),
			radial-gradient(ellipse 50% 45% at 90% 8%, oklch(0.55 0.22 300 / 0.14) 0%, transparent 60%);
	}
</style>
