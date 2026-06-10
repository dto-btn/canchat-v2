import { describe, it, expect } from 'vitest';
import { processResponseContent } from './index';

describe('processResponseContent — undelimited TeX wrapping', () => {
	// The exact failing case from the user report
	it('wraps \\boxed{...} in $ delimiters', () => {
		const input = '**Final Answer:**\n\\boxed{4\\ \\text{gallons}}';
		const output = processResponseContent(input);
		expect(output).toContain('$\\boxed{4\\ \\text{gallons}}$');
		// The surrounding markdown should be untouched
		expect(output).toContain('**Final Answer:**');
	});

	it('wraps \\frac{...}{...} in $ delimiters', () => {
		const input = 'The answer is \\frac{1}{2} of the total.';
		const output = processResponseContent(input);
		expect(output).toContain('$\\frac{1}{2}$');
	});

	it('wraps \\sqrt{...} in $ delimiters', () => {
		const input = 'The hypotenuse is \\sqrt{a^2 + b^2}.';
		const output = processResponseContent(input);
		expect(output).toContain('$\\sqrt{a^2 + b^2}$');
	});

	it('does not double-wrap already-delimited inline math', () => {
		const input = 'Already wrapped: $\\boxed{x}$';
		const output = processResponseContent(input);
		// Should not become $$\boxed{x}$$ or $$$\boxed{x}$$$
		expect(output).toBe('Already wrapped: $\\boxed{x}$');
	});

	it('does not double-wrap already-delimited block math', () => {
		const input = '$$\n\\boxed{x}\n$$';
		const output = processResponseContent(input);
		expect(output).toBe('$$\n\\boxed{x}\n$$');
	});

	it('does not touch TeX inside a code block', () => {
		const input = '```\n\\boxed{not math}\n```';
		const output = processResponseContent(input);
		expect(output).toBe('```\n\\boxed{not math}\n```');
	});

	it('does not touch TeX inside inline code', () => {
		const input = 'Use the `\\boxed{x}` command in LaTeX.';
		const output = processResponseContent(input);
		expect(output).toBe('Use the `\\boxed{x}` command in LaTeX.');
	});

	it('handles nested braces in \\boxed', () => {
		const input = '\\boxed{\\frac{1}{2}}';
		const output = processResponseContent(input);
		expect(output).toContain('$\\boxed{\\frac{1}{2}}$');
	});

	it('trims leading/trailing whitespace', () => {
		expect(processResponseContent('  hello  ')).toBe('hello');
	});
});
