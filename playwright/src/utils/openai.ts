import fs from 'fs';
import OpenAI from 'openai';

let openai: OpenAI | null = null;

function getOpenAI(): OpenAI {
	if (!openai) {
		if (!process.env.OPENAI_API_KEY) {
			throw new Error(
				'OPENAI_API_KEY environment variable is not set. Skipping image description.'
			);
		}
		openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
	}
	return openai;
}

export async function describeLocalImage(fileName: string): Promise<string> {
	const imageBase64 = fs.readFileSync(fileName).toString('base64');

	const response = await getOpenAI().chat.completions.create({
		model: 'gpt-4o-mini',
		messages: [
			{
				role: 'user',
				content: [
					{ type: 'text', text: 'Describe this image in detail.' },
					{
						type: 'image_url',
						image_url: {
							url: `data:image/jpeg;base64,${imageBase64}`
						}
					}
				]
			}
		],
		max_tokens: 300
	});

	return response.choices[0].message.content || 'Error: No description generated.';
}
