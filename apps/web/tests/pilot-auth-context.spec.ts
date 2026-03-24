import { expect, test } from '@playwright/test';

import { readApiResponse } from '../app/pilot-auth-context';

test.describe('readApiResponse', () => {
  test('parses JSON responses when the content type is application/json', async () => {
    const response = new Response(JSON.stringify({ detail: 'Invalid email or password.' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });

    await expect(readApiResponse<{ detail?: string }>(response)).resolves.toEqual({
      detail: 'Invalid email or password.',
    });
  });

  test('falls back to plain text when a JSON response body is not valid JSON', async () => {
    const response = new Response('Internal Server Error', {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });

    await expect(readApiResponse<{ detail?: string }>(response)).resolves.toEqual({
      detail: 'Internal Server Error',
    });
  });

  test('returns a fallback detail message for empty non-JSON responses', async () => {
    const response = new Response('', {
      status: 500,
      headers: { 'Content-Type': 'text/plain' },
    });

    await expect(readApiResponse<{ detail?: string }>(response)).resolves.toEqual({
      detail: 'Request failed with HTTP 500',
    });
  });
});
