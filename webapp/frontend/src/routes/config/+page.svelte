<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { parse } from 'smol-toml';
	import * as Card from '$lib/components/ui/card';
	import * as Tabs from '$lib/components/ui/tabs';
	import * as Table from '$lib/components/ui/table';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { Switch } from '$lib/components/ui/switch';
	import { Skeleton } from '$lib/components/ui/skeleton';
	import { apiGet, apiPut, type TomlOut, type EnvOut, type EnvEntry } from '$lib/api';
	import { SECTIONS, type Field } from '$lib/configSchema';

	// TOML state
	let toml = $state<TomlOut | null>(null);
	let parsed = $state<Record<string, Record<string, unknown>>>({});
	let tomlBusy = $state(false);
	let tomlRaw = $state('');
	let showRaw = $state(false);

	// env state
	let env = $state<EnvOut | null>(null);
	let editKey = $state<string | null>(null);
	let editValue = $state('');
	let envBusy = $state(false);

	async function loadToml() {
		const t = await apiGet<TomlOut>('/api/config/toml');
		toml = t;
		tomlRaw = t.content;
		parsed = parse(t.content) as Record<string, Record<string, unknown>>;
	}

	async function loadEnv() {
		env = await apiGet<EnvOut>('/api/config/env');
	}

	async function loadAll() {
		try {
			await Promise.all([loadToml(), loadEnv()]);
		} catch (e) {
			toast.error('load failed', { description: (e as Error).message });
		}
	}

	async function saveSection(sectionKey: string) {
		tomlBusy = true;
		try {
			// Send only the section's key/value dict. Backend uses tomlkit
			// to patch in place so hand-authored comments + column
			// alignment survive the round-trip.
			const fields = parsed[sectionKey] ?? {};
			const updated = await apiPut<TomlOut>('/api/config/toml/section', {
				section: sectionKey,
				fields
			});
			toml = updated;
			tomlRaw = updated.content;
			parsed = parse(updated.content) as Record<string, Record<string, unknown>>;
			toast.success(`[${sectionKey}] saved`);
		} catch (err) {
			toast.error('save failed', { description: (err as Error).message });
			await loadToml();
		} finally {
			tomlBusy = false;
		}
	}

	async function saveRaw() {
		tomlBusy = true;
		try {
			const updated = await apiPut<TomlOut>('/api/config/toml', { content: tomlRaw });
			toml = updated;
			tomlRaw = updated.content;
			parsed = parse(updated.content) as Record<string, Record<string, unknown>>;
			toast.success('config.toml saved');
		} catch (err) {
			toast.error('save failed', { description: (err as Error).message });
		} finally {
			tomlBusy = false;
		}
	}

	function getVal(section: string, key: string): unknown {
		return parsed[section]?.[key];
	}

	function setVal(section: string, key: string, v: unknown) {
		if (!parsed[section]) parsed[section] = {};
		parsed[section][key] = v;
		// trigger reactivity
		parsed = { ...parsed };
	}

	function toArray(v: unknown): string[] {
		if (Array.isArray(v)) return v.map(String);
		return [];
	}

	function fieldId(section: string, key: string): string {
		return `f-${section}-${key}`;
	}

	function startEditEnv(e: EnvEntry) {
		editKey = e.key;
		editValue = e.is_secret ? '' : e.value_masked;
	}

	async function saveEnv() {
		if (!editKey) return;
		envBusy = true;
		try {
			await apiPut(`/api/config/env/${editKey}`, { value: editValue });
			toast.success(`${editKey} updated`);
			editKey = null;
			editValue = '';
			await loadEnv();
		} catch (err) {
			toast.error('env save failed', { description: (err as Error).message });
		} finally {
			envBusy = false;
		}
	}

	onMount(loadAll);
</script>

<div class="space-y-6">
	<div class="flex items-baseline justify-between">
		<h1 class="text-2xl font-semibold tracking-tight">Config</h1>
		<div class="flex items-center gap-2">
			<Button variant="outline" size="sm" onclick={() => (showRaw = !showRaw)}>
				{showRaw ? 'Structured' : 'Raw TOML'}
			</Button>
			<Button variant="outline" size="sm" onclick={loadAll}>Reload</Button>
		</div>
	</div>

	<Tabs.Root value="toml">
		<Tabs.List>
			<Tabs.Trigger value="toml">config.toml</Tabs.Trigger>
			<Tabs.Trigger value="env">.env</Tabs.Trigger>
		</Tabs.List>

		<Tabs.Content value="toml">
			{#if toml === null}
				<Skeleton class="h-96 w-full" />
			{:else if showRaw}
				<Card.Root>
					<Card.Header>
						<Card.Title>Raw editor</Card.Title>
						<Card.Description>
							<span class="font-mono text-xs">{toml.path}</span> — validated
							against core.config.load_config before atomic swap.
						</Card.Description>
					</Card.Header>
					<Card.Content>
						<textarea
							bind:value={tomlRaw}
							spellcheck="false"
							class="w-full h-[70vh] rounded border bg-background p-3 font-mono text-xs"
						></textarea>
					</Card.Content>
					<Card.Footer class="flex justify-end gap-2">
						<Button variant="outline" onclick={() => (tomlRaw = toml!.content)} disabled={tomlBusy}>
							Revert
						</Button>
						<Button onclick={saveRaw} disabled={tomlBusy}>Save raw TOML</Button>
					</Card.Footer>
				</Card.Root>
			{:else}
				<div class="grid gap-4">
					{#each SECTIONS as section (section.key)}
						<Card.Root>
							<Card.Header>
								<Card.Title>[{section.key}] {section.title}</Card.Title>
								{#if section.description}
									<Card.Description>{section.description}</Card.Description>
								{/if}
							</Card.Header>
							<Card.Content class="grid gap-5">
								{#each section.fields as f (f.key)}
									{@const val = getVal(section.key, f.key)}
									<div class="grid grid-cols-1 md:grid-cols-3 gap-3 md:items-start">
										<div class="md:col-span-1">
											<label class="text-sm font-medium" for={fieldId(section.key, f.key)}>
												{f.label}
											</label>
											<p class="text-xs text-muted-foreground mt-1">{f.description}</p>
											<code class="text-[10px] text-muted-foreground">{f.key}</code>
										</div>
										<div class="md:col-span-2">
											{#if f.kind === 'boolean'}
												<Switch
													checked={val === true}
													onCheckedChange={(v) => setVal(section.key, f.key, v)}
												/>
											{:else if f.kind === 'select'}
												<select
													id={fieldId(section.key, f.key)}
													value={val ?? ''}
													onchange={(e) =>
														setVal(section.key, f.key, (e.currentTarget as HTMLSelectElement).value)}
													class="w-full rounded border bg-background px-3 py-2 text-sm"
												>
													{#each f.options ?? [] as opt}
														<option value={opt}>{opt}</option>
													{/each}
												</select>
											{:else if f.kind === 'number'}
												<input
													id={fieldId(section.key, f.key)}
													type="number"
													value={val ?? ''}
													min={f.min}
													max={f.max}
													step={f.step ?? 1}
													oninput={(e) => {
														const raw = (e.currentTarget as HTMLInputElement).value;
														setVal(section.key, f.key, raw === '' ? '' : Number(raw));
													}}
													class="w-full rounded border bg-background px-3 py-2 text-sm"
												/>
											{:else if f.kind === 'text-array'}
												<textarea
													id={fieldId(section.key, f.key)}
													value={toArray(val).join('\n')}
													oninput={(e) =>
														setVal(
															section.key,
															f.key,
															(e.currentTarget as HTMLTextAreaElement).value
																.split('\n')
																.map((s) => s.trim())
																.filter(Boolean)
														)}
													rows={Math.min(6, Math.max(2, toArray(val).length + 1))}
													spellcheck="false"
													class="w-full rounded border bg-background px-3 py-2 font-mono text-xs"
												></textarea>
												<p class="text-[10px] text-muted-foreground mt-1">one per line</p>
											{:else}
												<input
													id={fieldId(section.key, f.key)}
													type="text"
													value={(val as string | number | null) ?? ''}
													oninput={(e) =>
														setVal(
															section.key,
															f.key,
															(e.currentTarget as HTMLInputElement).value
														)}
													class="w-full rounded border bg-background px-3 py-2 text-sm"
												/>
											{/if}
										</div>
									</div>
								{/each}
							</Card.Content>
							<Card.Footer class="flex justify-end">
								<Button onclick={() => saveSection(section.key)} disabled={tomlBusy}>
									Save {section.key}
								</Button>
							</Card.Footer>
						</Card.Root>
					{/each}
				</div>
			{/if}
		</Tabs.Content>

		<Tabs.Content value="env">
			<Card.Root>
				<Card.Header>
					<Card.Title>Environment secrets</Card.Title>
					<Card.Description>
						Secret values render masked. Editing writes a fresh value; comments
						and order preserved.
						{#if env}<span class="font-mono text-xs pl-2">{env.path}</span>{/if}
					</Card.Description>
				</Card.Header>
				<Card.Content>
					{#if env === null}
						<Skeleton class="h-64 w-full" />
					{:else}
						<Table.Root>
							<Table.Header>
								<Table.Row>
									<Table.Head>key</Table.Head>
									<Table.Head>value</Table.Head>
									<Table.Head class="text-right">edit</Table.Head>
								</Table.Row>
							</Table.Header>
							<Table.Body>
								{#each env.entries as e (e.key)}
									<Table.Row>
										<Table.Cell class="font-mono text-xs">
											{e.key}
											{#if e.is_secret}<Badge variant="secondary" class="ml-2">secret</Badge>{/if}
										</Table.Cell>
										<Table.Cell class="font-mono text-xs">
											{#if editKey === e.key}
												<input
													type={e.is_secret ? 'password' : 'text'}
													bind:value={editValue}
													placeholder={e.is_secret ? 'new value' : ''}
													class="w-full rounded border bg-background px-2 py-1 text-xs"
												/>
											{:else}
												{e.value_masked || '—'}
											{/if}
										</Table.Cell>
										<Table.Cell class="text-right space-x-2">
											{#if editKey === e.key}
												<Button size="sm" onclick={saveEnv} disabled={envBusy}>Save</Button>
												<Button
													size="sm"
													variant="outline"
													onclick={() => {
														editKey = null;
														editValue = '';
													}}
												>
													Cancel
												</Button>
											{:else}
												<Button size="sm" variant="outline" onclick={() => startEditEnv(e)}>
													Edit
												</Button>
											{/if}
										</Table.Cell>
									</Table.Row>
								{/each}
							</Table.Body>
						</Table.Root>
					{/if}
				</Card.Content>
			</Card.Root>
		</Tabs.Content>
	</Tabs.Root>
</div>
