// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
	integrations: [
		starlight({
			title: 'Inspector',
			description: 'Design of the agentic validator for the ThoughtSpot → Strategy (Mosaic) migration.',
			favicon: '/favicon.svg',
			logo: { src: './src/assets/logo.svg', alt: 'Inspector logo' },
			customCss: ['./src/styles/custom.css'],
			lastUpdated: false,
			sidebar: [
				{ label: 'Overview', link: '/' },
				{
					label: 'The problem',
					items: [
						{ label: 'Why lift-and-shift breaks', slug: 'problem/why-it-breaks' },
						{ label: 'The core idea', slug: 'problem/core-idea' },
					],
				},
				{
					label: 'Architecture',
					items: [
						{ label: 'System overview', slug: 'architecture/overview' },
						{ label: 'Team & A2A', slug: 'architecture/a2a' },
						{ label: 'Repository layout', slug: 'architecture/repository' },
						{ label: 'LLM integration', slug: 'architecture/llm' },
						{ label: 'Packaging & runtime', slug: 'architecture/runtime' },
					],
				},
				{
					label: 'Inputs',
					items: [
						{ label: 'Migration package', slug: 'inputs/migration-package' },
						{ label: 'Strategy formats', slug: 'inputs/strategy-formats' },
						{ label: 'Mapping file', slug: 'inputs/mapping-file' },
					],
				},
				{
					label: 'Engine',
					items: [
						{ label: 'Intermediate representation', slug: 'engine/ir' },
						{ label: 'Lineage resolution', slug: 'engine/lineage' },
						{ label: 'Joins, grain & fan-out', slug: 'engine/joins-and-grain' },
						{ label: 'Object matching', slug: 'engine/matching' },
					],
				},
				{
					label: 'Validation',
					items: [
						{ label: 'Check catalogue', slug: 'validation/checks' },
						{ label: 'Verdict policy', slug: 'validation/verdict-policy' },
					],
				},
				{
					label: 'Agents',
					items: [
						{ label: 'inspector_agent', slug: 'agents/inspector-agent' },
						{ label: 'thoughtspot_agent', slug: 'agents/thoughtspot-agent' },
						{ label: 'strategy_agent', slug: 'agents/strategy-agent' },
						{ label: 'data_agent', slug: 'agents/data-agent' },
					],
				},
				{ label: 'Report', slug: 'output/report' },
				{ label: 'Synthetic data & evaluation', slug: 'quality/synthetic-data' },
				{
					label: 'Plan',
					items: [
						{ label: 'Delivery plan', slug: 'plan/delivery' },
						{ label: 'Assumptions & risks', slug: 'plan/risks' },
					],
				},
			],
		}),
	],
});
