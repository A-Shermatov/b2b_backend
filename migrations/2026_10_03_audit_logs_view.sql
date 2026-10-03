INSERT INTO public.permissions (name, resource, action)
SELECT 'audit_logs.view', 'audit_logs', 'view'
WHERE NOT EXISTS (SELECT 1 FROM public.permissions p WHERE p.name = 'audit_logs.view');

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.name = 'audit_logs.view'
WHERE r.name IN ('admin', 'superadmin')
  AND NOT EXISTS (
      SELECT 1 FROM public.role_permissions rp
      WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

CREATE INDEX IF NOT EXISTS audit_logs_created_at_idx
    ON public.audit_logs (created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS audit_logs_user_created_idx
    ON public.audit_logs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS audit_logs_http_created_idx
    ON public.audit_logs (created_at DESC, id DESC)
    WHERE action LIKE 'http.%';
