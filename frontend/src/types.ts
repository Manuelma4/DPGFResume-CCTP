export type AnalysisStatus =
  | 'queued'
  | 'processing'
  | 'needs_review'
  | 'ready'
  | 'failed';

export interface AuthUser {
  sub: string;
  name: string;
  email: string;
  username: string;
  is_local: boolean;
  auth_required: boolean;
  environment: string;
}

export interface Project {
  name: string;
  reference: string;
  client: string;
  phase: string;
  due_date: string;
}

export interface SourceDocument {
  id: string;
  original_name: string;
  stored_name: string;
  extension: string;
  size: number;
  sha256: string;
  status: 'uploaded' | 'processed' | 'failed';
  page_count?: number;
  character_count?: number;
  error?: string;
}

export interface DpgfLine {
  id: string;
  kind: 'section' | 'item';
  level: number;
  code: string;
  designation: string;
  description: string;
  unit: string | null;
  quantity: number | null;
  unit_price?: number | null;
  included: boolean;
  confidence: number;
  review_status: 'validated' | 'to_review';
  review_reason: string;
  source_id: string;
  source_page: number | null;
  source_excerpt: string;
  origin: string;
  unit_source?: 'explicit' | 'rule' | 'llm' | 'skeleton' | 'default' | null;
  unit_confidence?: number | null;
  quantity_source?: 'explicit' | 'missing' | null;
  review_fields?: string[];
  user_edited?: boolean;
}

export interface Lot {
  id: string;
  code: string;
  title: string;
  source_id: string;
  lines: DpgfLine[];
  warnings: string[];
}

export interface AnalysisStats {
  lots: number;
  sections: number;
  items: number;
  to_review: number;
  validated: number;
  unit_coverage: number;
  average_confidence: number;
  quantity_coverage?: number;
  classification_confidence?: number;
  accepted_ratio?: number;
}

export interface AnalysisWarning {
  source_id: string;
  source_name: string;
  message: string;
}

export interface AnalysisOwner {
  sub: string;
  name: string;
  email: string;
}

export interface AnalysisShare {
  email: string;
  name: string;
  permission: 'view' | 'edit';
}

export interface DirectoryUser {
  email: string;
  name: string;
  username: string;
}

export interface Analysis {
  id: string;
  project: Project;
  status: AnalysisStatus;
  progress: number;
  created_at: string;
  updated_at: string;
  documents: SourceDocument[];
  lots: Lot[];
  stats: AnalysisStats;
  warnings: AnalysisWarning[];
  processing: {
    method: string;
    llm_requested: boolean;
    llm_used: boolean;
  };
  error: string;
  export_name: string;
  owner?: AnalysisOwner;
  can_edit: boolean;
  can_manage_sharing: boolean;
  shares: AnalysisShare[];
}

export interface AnalysisListItem {
  id: string;
  project_name: string;
  project_reference: string;
  client_name: string;
  phase: string;
  due_date: string;
  status: AnalysisStatus;
  progress: number;
  created_at: string;
  updated_at: string;
  document_count: number;
  lot_count: number;
  line_count: number;
  review_count: number;
  error: string;
  has_export: boolean;
  owner_name: string;
  can_edit: boolean;
  can_manage_sharing: boolean;
}
