import type { MetricsModelOption } from '$lib/apis/metrics';

// Model families for the Metrics "Model Analysis" dropdown (display only).
// TODO: use the planned model_family column instead of these name patterns.

// Order matters: first match wins, and groups are shown in this order
const FAMILIES: { name: string; pattern: RegExp }[] = [
	{ name: 'GPT', pattern: /(gpt|^o\d|[/.:-]o\d|openai|davinci)/i },
	// "poseidon" was the model behind Cohere Command-A (retired May 2026)
	{ name: 'Cohere', pattern: /(cohere|command|embed-(english|multilingual)|poseidon)/i },
	{ name: 'Claude', pattern: /(claude|anthropic)/i },
	{ name: 'Gemini', pattern: /(gemini|gemma|palm)/i },
	{ name: 'Llama', pattern: /llama/i },
	{ name: 'Mistral', pattern: /(mistral|mixtral|codestral|ministral|pixtral)/i },
	{ name: 'DeepSeek', pattern: /deepseek/i },
	{ name: 'Phi', pattern: /(^|[/.:-])phi/i },
	{ name: 'Qwen', pattern: /qwen/i }
];

// Models matching no family
export const OTHER_FAMILY = 'Other';

const FAMILY_ORDER = [...FAMILIES.map((f) => f.name), OTHER_FAMILY];

// Check the id first: admins can give custom models any display name
export const getModelFamily = (model: MetricsModelOption): string => {
	const family =
		FAMILIES.find((f) => f.pattern.test(model.id)) ??
		FAMILIES.find((f) => f.pattern.test(model.name));
	return family?.name ?? OTHER_FAMILY;
};

export type ModelFamilyGroup = { name: string; models: MetricsModelOption[] };

// Groups models by family, in display order; empty families are left out
export const groupModelsByFamily = (models: MetricsModelOption[]): ModelFamilyGroup[] => {
	const map = new Map<string, MetricsModelOption[]>();
	for (const model of models) {
		const family = getModelFamily(model);
		if (!map.has(family)) map.set(family, []);
		map.get(family)!.push(model);
	}
	return FAMILY_ORDER.filter((name) => map.has(name)).map((name) => ({
		name,
		models: map.get(name)!
	}));
};
