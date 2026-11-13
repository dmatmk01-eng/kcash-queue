-- KCash: ตารางซิงก์ แผนจัดคิว / หมายเหตุ / รายการไม่อนุมัติ / จ่ายแล้วรออัพเดต
-- วางใน Supabase > SQL Editor แล้วกด Run (รันซ้ำได้ ไม่เสียหาย)
create table if not exists kcash_sync (
  ns text not null,
  k text not null,
  v jsonb,
  deleted boolean not null default false,
  updated_by text default '',
  updated_at timestamptz not null default now(),
  primary key (ns, k)
);
alter table kcash_sync enable row level security;
drop policy if exists "Allow all with anon key" on kcash_sync;
create policy "Allow all with anon key" on kcash_sync
  for all using (true) with check (true);
