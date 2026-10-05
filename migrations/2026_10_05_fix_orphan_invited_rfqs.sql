UPDATE public.rfqs r
SET visibility = 'public'::rfq_visibility, updated_at = NOW()
WHERE r.visibility::text = 'invited_only'
  AND r.status::text <> 'draft'
  AND NOT EXISTS (
      SELECT 1 FROM public.rfq_invited_suppliers i WHERE i.rfq_id = r.id
  );
