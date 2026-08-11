import type { Analysis, AnalysisListItem, AnalysisShare, AuthUser, DirectoryUser } from './types';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin', ...init });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(data.detail || `Erreur HTTP ${response.status}`, response.status);
  }
  return data as T;
}

export function getCurrentUser(signal?: AbortSignal): Promise<AuthUser> {
  return requestJson<AuthUser>('/api/auth/me', { signal });
}

export function listAnalyses(search = ''): Promise<AnalysisListItem[]> {
  const query = search ? `?search=${encodeURIComponent(search)}` : '';
  return requestJson<{ analyses: AnalysisListItem[] }>(`/api/v1/analyses${query}`)
    .then((response) => response.analyses);
}

export function getAnalysis(id: string, signal?: AbortSignal): Promise<Analysis> {
  return requestJson<Analysis>(`/api/v1/analyses/${id}`, { signal });
}

export interface NewAnalysisData {
  projectName: string;
  projectReference: string;
  clientName: string;
  phase: string;
  dueDate: string;
  files: File[];
}

export function createAnalysis(data: NewAnalysisData): Promise<{ id: string }> {
  const body = new FormData();
  body.append('project_name', data.projectName);
  body.append('project_reference', data.projectReference);
  body.append('client_name', data.clientName);
  body.append('phase', data.phase);
  body.append('due_date', data.dueDate);
  data.files.forEach((file) => body.append('files', file));
  return requestJson('/api/v1/analyses', { method: 'POST', body });
}

export function saveAnalysis(analysis: Analysis): Promise<Analysis> {
  return requestJson<Analysis>(`/api/v1/analyses/${analysis.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project: analysis.project, lots: analysis.lots }),
  });
}

export function reprocessAnalysis(id: string): Promise<{ id: string }> {
  return requestJson(`/api/v1/analyses/${id}/reprocess`, { method: 'POST' });
}

export function generateExport(id: string): Promise<{ download_url: string; review_count: number }> {
  return requestJson(`/api/v1/analyses/${id}/export`, { method: 'POST' });
}

export async function deleteAnalysis(id: string): Promise<void> {
  const response = await fetch(`/api/v1/analyses/${id}`, {
    method: 'DELETE',
    credentials: 'same-origin',
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(data.detail || 'Suppression impossible', response.status);
  }
}

export function listDirectoryUsers(query = ''): Promise<DirectoryUser[]> {
  const search = query ? `?q=${encodeURIComponent(query)}` : '';
  return requestJson<{ users: DirectoryUser[] }>(`/api/v1/directory/users${search}`)
    .then((response) => response.users);
}

export function updateAnalysisShares(id: string, shares: AnalysisShare[]): Promise<AnalysisShare[]> {
  return requestJson<{ shares: AnalysisShare[] }>(`/api/v1/analyses/${id}/shares`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ shares }),
  }).then((response) => response.shares);
}
