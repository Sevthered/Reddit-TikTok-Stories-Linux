// Editable descriptors for config.toml. Each field renders as one form
// row on /config. Keep in sync with core/config.py — new fields there
// won't crash the editor but they'll go unlabelled until added here.

export type FieldKind = 'text' | 'number' | 'boolean' | 'select' | 'text-array';

export type Field = {
	key: string;
	label: string;
	description: string;
	kind: FieldKind;
	options?: string[]; // for select
	min?: number;
	max?: number;
	step?: number;
};

export type Section = {
	key: string;
	title: string;
	description?: string;
	fields: Field[];
};

export const SECTIONS: Section[] = [
	{
		key: 'reddit',
		title: 'Reddit',
		description: 'Source of raw stories.',
		fields: [
			{
				key: 'mode',
				label: 'Fetch mode',
				description: 'json = unauth .json (rate-limited). praw = OAuth (needs .env keys). rss = Atom feed, no score.',
				kind: 'select',
				options: ['json', 'praw', 'rss']
			},
			{
				key: 'subreddits',
				label: 'Subreddits',
				description: 'Comma-separated list of subs to scrape.',
				kind: 'text-array'
			},
			{
				key: 'listing',
				label: 'Listing',
				description: 'top = highest-scored. hot = trending. new = most recent.',
				kind: 'select',
				options: ['top', 'hot', 'new']
			},
			{
				key: 'time_filter',
				label: 'Time filter',
				description: 'Only for `top`. hour|day|week|month|year|all.',
				kind: 'select',
				options: ['hour', 'day', 'week', 'month', 'year', 'all']
			},
			{
				key: 'limit',
				label: 'Limit',
				description: 'Posts fetched per subreddit per run.',
				kind: 'number',
				min: 1,
				max: 200
			},
			{
				key: 'user_agent',
				label: 'User agent',
				description: 'Reddit rejects generic UAs — keep the contact email.',
				kind: 'text'
			}
		]
	},
	{
		key: 'filter',
		title: 'Filter',
		description: 'Story-shape gate before render.',
		fields: [
			{ key: 'min_words', label: 'Min words', description: 'Reject stories shorter than this.', kind: 'number', min: 10 },
			{ key: 'max_words', label: 'Max words', description: 'Reject stories longer than this.', kind: 'number', min: 50 },
			{ key: 'min_score', label: 'Min upvotes', description: 'Score floor. `rss` mode ignores this.', kind: 'number', min: 0 },
			{ key: 'allow_nsfw', label: 'Allow NSFW', description: 'Include over_18 posts.', kind: 'boolean' },
			{
				key: 'profanity_mode',
				label: 'Profanity',
				description: 'off = keep as-is. soft = mask **. strict = skip the post entirely.',
				kind: 'select',
				options: ['off', 'soft', 'strict']
			},
			{
				key: 'confusable_mode',
				label: 'Confusables',
				description: 'off | sanitize (map Cyrillic/Greek look-alikes → Latin) | strict (skip if any).',
				kind: 'select',
				options: ['off', 'sanitize', 'strict']
			}
		]
	},
	{
		key: 'tts',
		title: 'TTS',
		description: 'Voice-over synthesis.',
		fields: [
			{
				key: 'engine',
				label: 'Engine',
				description: 'edge = Microsoft edge-tts (fast, cloud). kokoro = local MLX model.',
				kind: 'select',
				options: ['edge', 'kokoro']
			},
			{
				key: 'voice',
				label: 'Voice',
				description: 'edge: e.g. `en-US-GuyNeural`. kokoro: local voice slug.',
				kind: 'text'
			},
			{ key: 'rate', label: 'Rate', description: 'Playback rate delta, e.g. `+8%` (edge only).', kind: 'text' },
			{
				key: 'pause_between_sentences_ms',
				label: 'Sentence pause (ms)',
				description: 'Silence gap between sentences.',
				kind: 'number',
				min: 0,
				max: 2000
			}
		]
	},
	{
		key: 'whisper',
		title: 'Whisper',
		description: 'Word-level caption alignment.',
		fields: [
			{
				key: 'backend',
				label: 'Backend',
				description: 'mlx = Apple Silicon accelerated. faster = CPU/CUDA fallback.',
				kind: 'select',
				options: ['mlx', 'faster']
			},
			{
				key: 'model',
				label: 'Model',
				description: 'Whisper size: `small.en`, `medium.en`, `large-v3`, …',
				kind: 'text'
			},
			{ key: 'word_level', label: 'Word-level timing', description: 'Required for karaoke captions.', kind: 'boolean' }
		]
	},
	{
		key: 'captions',
		title: 'Captions',
		description: 'ASS/SSA style burned into the video.',
		fields: [
			{ key: 'font', label: 'Font family', description: 'System font name.', kind: 'text' },
			{ key: 'font_size', label: 'Font size', description: '1080×1920 PlayRes; TikTok samples land near 60–90.', kind: 'number', min: 20, max: 200 },
			{ key: 'primary_color', label: 'Primary color', description: 'ASS `&HAABBGGRR` (white = `&H00FFFFFF`).', kind: 'text' },
			{ key: 'highlight', label: 'Highlight color', description: 'Word-under-cursor color.', kind: 'text' },
			{ key: 'outline', label: 'Outline (px)', description: 'Heavier outline stays legible over busy gameplay.', kind: 'number', min: 0, max: 20 },
			{ key: 'words_per_cue', label: 'Words / cue', description: 'Group N words per subtitle.', kind: 'number', min: 1, max: 6 },
			{ key: 'margin_v', label: 'Vertical margin', description: 'Distance from bottom, in PlayRes px.', kind: 'number', min: 0, max: 1920 },
			{ key: 'case', label: 'Case', description: 'upper | lower | preserve.', kind: 'select', options: ['upper', 'lower', 'preserve'] },
			{ key: 'highlight_mode', label: 'Highlight mode', description: 'color = fill. box = background box (deferred).', kind: 'select', options: ['color', 'box'] }
		]
	},
	{
		key: 'background',
		title: 'Background',
		description: 'Parkour B-roll pool.',
		fields: [
			{ key: 'source_urls', label: 'Source URLs', description: 'YouTube URLs of long parkour/no-copyright videos.', kind: 'text-array' },
			{ key: 'cache_dir', label: 'Cache dir', description: 'Where downloads land.', kind: 'text' },
			{ key: 'audio_volume', label: 'Audio volume', description: 'Background audio mix (0.0 = muted).', kind: 'number', min: 0, max: 1, step: 0.05 }
		]
	},
	{
		key: 'video',
		title: 'Video',
		description: 'ffmpeg output format.',
		fields: [
			{ key: 'width', label: 'Width', description: 'Portrait width (TikTok = 1080).', kind: 'number' },
			{ key: 'height', label: 'Height', description: 'Portrait height (TikTok = 1920).', kind: 'number' },
			{ key: 'fps', label: 'FPS', description: 'Frame rate.', kind: 'number', min: 15, max: 60 },
			{ key: 'video_bitrate', label: 'Video bitrate', description: 'ffmpeg -b:v (e.g. `10M`).', kind: 'text' }
		]
	}
];
