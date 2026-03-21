import apiClient from './client'
import type { DocumentListResponse, Document } from '@/lib/types'

export const documentsApi = {
  list: async (page = 1, pageSize = 20, status?: string): Promise<DocumentListResponse> => {
    const params: Record<string, any> = { page, page_size: pageSize }
    if (status) params.status = status
    const response = await apiClient.get('/v1/documents', { params })
    return response.data
  },

  get: async (documentId: string): Promise<Document> => {
    const response = await apiClient.get(`/v1/documents/${documentId}`)
    return response.data
  },

  // JSON-based ingest (for programmatic text)
  ingest: async (content: string, filename: string, contentType = 'text') => {
    const response = await apiClient.post('/v1/ingest', {
      content,
      filename,
      content_type: contentType,
    })
    return response.data
  },

  // File upload via multipart (preferred for files)
  uploadFile: async (file: File, metadata?: Record<string, string>) => {
    const formData = new FormData()
    formData.append('file', file)
    if (metadata) {
      formData.append('metadata_json', JSON.stringify(metadata))
    }

    const response = await apiClient.post('/v1/ingest/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000, // 2 min timeout for large files
    })
    return response.data
  },

  // Bulk file upload via multipart
  uploadFiles: async (files: File[]) => {
    const formData = new FormData()
    files.forEach((file) => {
      formData.append('files', file)
    })

    const response = await apiClient.post('/v1/ingest/upload/bulk', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300000, // 5 min timeout for bulk
    })
    return response.data
  },

  // Legacy JSON bulk (keep for backward compatibility)
  ingestBulk: async (documents: Array<{
    content: string
    filename: string
    content_type: string
  }>) => {
    const response = await apiClient.post('/v1/ingest/bulk', { documents })
    return response.data
  },

  getJobStatus: async (jobId: string) => {
    const response = await apiClient.get(`/v1/ingest/jobs/${jobId}`)
    return response.data
  },
}