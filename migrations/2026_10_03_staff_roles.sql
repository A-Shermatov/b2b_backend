ALTER TABLE public.roles ADD COLUMN IF NOT EXISTS scope character varying(20) NOT NULL DEFAULT 'staff';

UPDATE public.roles
SET scope = 'marketplace'
WHERE name IN ('buyer', 'supplier', 'both') AND scope <> 'marketplace';

INSERT INTO public.permissions (name, resource, action)
SELECT v.name, v.resource, v.action
FROM (
    VALUES
        ('admin_staff.view', 'admin_staff', 'view'),
        ('admin_staff.create', 'admin_staff', 'create'),
        ('admin_staff.update', 'admin_staff', 'update')
) AS v(name, resource, action)
WHERE NOT EXISTS (SELECT 1 FROM public.permissions p WHERE p.name = v.name);

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
JOIN public.permissions p ON p.resource = 'admin_staff'
WHERE r.name IN ('admin', 'superadmin')
  AND NOT EXISTS (
      SELECT 1 FROM public.role_permissions rp
      WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );
