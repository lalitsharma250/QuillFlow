import apiClient from './client'

export const adminApi = {
  // Stats
  getStats: async () => {
    const response = await apiClient.get('/v1/admin/stats')
    return response.data
  },

  // Users
  listUsers: async (includeInactive = false) => {
    const response = await apiClient.get('/v1/admin/users', {
      params: { include_inactive: includeInactive },
    })
    return response.data
  },

  createUser: async (data: { email: string; name: string; role: string }) => {
    const response = await apiClient.post('/v1/admin/users', data)
    return response.data
  },

  updateRole: async (userId: string, role: string) => {
    const response = await apiClient.patch(`/v1/admin/users/${userId}/role`, { role })
    return response.data
  },

  deactivateUser: async (userId: string) => {
    const response = await apiClient.delete(`/v1/admin/users/${userId}`)
    return response.data
  },

  // Invite Codes
  createInvite: async (data: { role: string; max_uses: number; expires_in_days: number }) => {
    const response = await apiClient.post('/v1/admin/invites', data)
    return response.data
  },

  listInvites: async (includeExpired = false) => {
    const response = await apiClient.get('/v1/admin/invites', {
      params: { include_expired: includeExpired },
    })
    return response.data
  },
    superadminCreateOrg: async (data: {
    name: string
    admin_email: string
    admin_name: string
    admin_password: string
  }) => {
    const response = await apiClient.post('/v1/admin/superadmin/orgs', data)
    return response.data
  },
  revokeInvite: async (code: string) => {
    const response = await apiClient.delete(`/v1/admin/invites/${code}`)
    return response.data
  },

  // Audit
  getAuditLogs: async (params?: { action?: string; user_id?: string; limit?: number }) => {
    const response = await apiClient.get('/v1/admin/audit', { params })
    return response.data
  },

  // System
  clearCache: async () => {
    const response = await apiClient.delete('/v1/admin/cache')
    return response.data
  },
    // Add inside adminApi object:
  reactivateUser: async (userId: string) => {
    const response = await apiClient.patch(`/v1/admin/users/${userId}/reactivate`)
    return response.data
  },
  superadminListOrgs: async () => {
    const response = await apiClient.get('/v1/admin/superadmin/orgs')
    return response.data
  },

  superadminListOrgUsers: async (orgId: string) => {
    const response = await apiClient.get(`/v1/admin/superadmin/orgs/${orgId}/users`)
    return response.data
  },

  superadminDeactivateOrg: async (orgId: string) => {
    const response = await apiClient.delete(`/v1/admin/superadmin/orgs/${orgId}`)
    return response.data
  },

  superadminReactivateOrg: async (orgId: string) => {
    const response = await apiClient.patch(`/v1/admin/superadmin/orgs/${orgId}/reactivate`)
    return response.data
  },
  cleanStaleDocuments: async (statusFilter = 'all_stale') => {
    const response = await apiClient.delete('/v1/admin/documents/stale', {
      params: { status_filter: statusFilter },
    })
    return response.data
  },

  cleanStaleJobs: async () => {
    const response = await apiClient.delete('/v1/admin/jobs/stale')
    return response.data
  },

  deleteDocument: async (documentId: string) => {
    const response = await apiClient.delete(`/v1/admin/documents/${documentId}`)
    return response.data
  },
}