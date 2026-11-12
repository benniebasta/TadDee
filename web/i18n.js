// Tiny i18n: t(key, vars, fallback). English + Thai; choice stored in localStorage.
const KEY = 'nest.lang';

const en = {
  // shell
  nav_jobs: 'Jobs', nav_relief: 'Relief', nav_offcuts: 'Offcuts', nav_settings: 'Settings', language: 'Language',
  loading: 'Loading…', working: 'Working…', ok: 'OK', cancel: 'Cancel', close: 'Close', save: 'Save', saved: 'Saved',
  edit: 'Edit', delete: 'Delete', deleted: 'Deleted', update: 'Update', none_yet: 'Nothing here yet.',
  // jobs
  new_job: 'New job', create_job: 'Create job', job_name: 'Job name', job_name_ph: 'e.g. Shop front letters',
  material: 'Material', machine: 'Machine', status: 'Status', sheets: 'Sheets', utilisation: 'Utilisation', created: 'Created',
  no_jobs: 'No jobs yet. Press “New job” to start.', err_job_name: 'Type a job name.', back_jobs: 'Back to jobs',
  status_draft: 'Draft', status_nested: 'Nested', status_exported: 'Exported', status_cut: 'Cut',
  delete_job: 'Delete job', confirm_delete_job: 'Delete job “{name}” and all its files?',
  // parts
  parts: 'Parts', choose_files: 'Choose files', drop_files: 'or drop SVG / DXF files here', file_types: 'SVG or DXF, up to 20 MB each',
  uploading: 'Uploading…', imported_n: '{n} parts imported', import_warnings: 'Import notes', no_parts: 'No parts yet. Add an SVG or DXF file.',
  name: 'Name', size_mm: 'Size (mm)', holes: 'Holes', qty: 'Qty', marks_n: '{n} marks ({layer})', err_qty: 'Quantity must be 0 to 999.',
  // nest
  nest: 'Nest', nesting: 'Nesting…', gap_mm: 'Gap between parts (mm)', gap_hint: 'CNC: bit diameter + 1 mm · laser: 2 mm',
  rotations: 'Rotations', rot_0_180: '0/180', rot_90: '0/90/180/270', rot_45: 'every 45°', rot_15: 'every 15°',
  quality: 'Quality', q_fast: 'Fast', q_normal: 'Normal', q_fine: 'Fine',
  parts_in_holes: 'Put small parts inside holes', use_offcuts: 'Use offcuts first',
  sheet_w: 'Sheet width (mm)', sheet_h: 'Sheet height (mm)', sheet_hint: 'Empty = material sheet size',
  not_nested: 'Not nested yet. Set the options and press Nest.',
  sheets_used: 'Sheets used', unplaced: 'Unplaced', cost: 'Cost', excl_vat: 'excl. VAT',
  sheets_cost: 'sheets', machine_cost: 'machine', set_price: 'Set a price in Settings', cut_time: 'about {m} min cutting',
  sheet: 'Sheet', offcut: 'Offcut', leftover: 'Leftover', parts_n: '{n} parts', click_zoom: 'Click to enlarge',
  unplaced_parts: 'These parts did not fit:', nest_meta: '{a} layouts tried in {s} s',
  // export
  export: 'Export', export_cnc: 'CNC router: {m}. One G-code file per sheet per bit.', export_laser: 'Laser: {m}. One DXF per sheet with CUT / SCORE / ENGRAVE layers.',
  cut_tool: 'Cut bit', mark_tool: 'Mark bit', same_as_cut: 'Same as cut bit', cut_through: 'Cut into spoil board (mm)',
  tabs: 'Tabs', tab_width: 'Tab width (mm)', tab_height: 'Tab height (mm)', climb: 'Climb milling',
  make_gcode: 'Make G-code', make_dxf: 'Make DXF', making: 'Making files…', kerf_mm: 'Kerf (mm)', sheet_outline: 'Include sheet outline',
  files: 'Files', no_files: 'No files yet.', download_zip: 'Download all (zip)', files_ready: 'Files ready: {n}',
  mark_cut: 'Mark as cut', already_cut: 'Marked as cut', marked_cut: 'Job marked as cut. Offcuts saved.',
  confirm_cut: 'Mark this job as cut? Leftover pieces go to the offcut library and used offcuts are taken out.',
  minutes_n: '{n} min',
  // relief
  relief_intro: 'Turn a flat picture (logo, sign artwork) into a 3D carving with rough and finish G-code.',
  picture: 'Picture', picture_hint: 'PNG or JPG with clear flat colours', width_mm: 'Width (mm)', material_mm: 'Material thickness (mm)',
  max_height_mm: 'Max relief height (mm)', rough_bit: 'Rough bit', finish_bit: 'Finish bit', no_rough: 'No roughing',
  kind_flat: 'flat', kind_ball: 'ball nose', kind_vbit: 'V-bit', kind_cnc: 'CNC router', kind_laser: 'Laser',
  make_relief: 'Make relief', making_relief: 'Making relief…', err_pick_image: 'Choose a picture first.',
  err_relief_sizes: 'Fill in width and material thickness.', relief_updated: 'Relief updated',
  size: 'Size', depth: 'Depth', lost_detail: 'Detail the bit cannot reach', total_time: 'Total time', preview: 'Relief preview',
  regions: 'Colour regions', colour: 'Colour', area_cm2: 'Area (cm²)', profile: 'Shape', height_mm: 'Height (mm)', round_mm: 'Round (mm)',
  prof_dome: 'Dome', prof_flat: 'Flat', prof_bevel: 'Bevel', prof_engrave: 'Engrave', prof_background: 'Background', images: 'Images',
  // offcuts
  offcuts_intro: 'Leftover pieces saved when a job is marked as cut. Nest uses them first when “Use offcuts first” is on.',
  all_materials: 'All materials', show_used: 'Show used', no_offcuts: 'No offcuts saved.', from_job: 'From job', job_n: 'Job {n}',
  used: 'Used', free: 'Free', confirm_delete_offcut: 'Delete this offcut from the library?',
  // settings
  machines: 'Machines', tools: 'Bits', materials: 'Materials', add_machine: 'Add machine', add_tool: 'Add bit', add_material: 'Add material',
  kind: 'Type', bed_w: 'Bed width (mm)', bed_h: 'Bed height (mm)', bed_mm: 'Bed (mm)', rate_per_hour: 'Rate (THB/h, excl. VAT)', notes: 'Notes',
  diameter_mm: 'Diameter (mm)', angle_deg: 'V angle (°)', pass_depth_mm: 'Depth per pass (mm)', feed_mm_min: 'Feed (mm/min)',
  plunge_mm_min: 'Plunge (mm/min)', rpm: 'Spindle (rpm)', thickness_mm: 'Thickness (mm)', sheet_mm: 'Sheet (mm)', margin_mm: 'Edge margin (mm)',
  price_per_sheet: 'Price per sheet (THB, excl. VAT)', price_hint: '0 = not set yet', set_price_short: 'set price',
  edit_x: 'Edit {name}', confirm_delete: 'Delete “{name}”?', err_required: '{f} is required.', err_number: '{f} must be a number.',
  // workspace (makeover)
  theme_light: 'Light workspace', theme_dark: 'Dark workspace', click_show: 'Show this sheet',
  parts_board: 'All parts · {n} copies to cut', canvas_empty_title: 'No parts yet',
  canvas_empty: 'Drop SVG or DXF files here, or click to choose', copies_n: '{n} copies', cut: 'Cutting',
  relief_canvas_empty_title: 'No picture yet', relief_canvas_empty: 'Choose a picture to carve (PNG or JPG)',
  relief_regions_empty: 'Make the relief first; each colour of the picture then gets its own shape here.',
  relief_status_empty: 'Choose a picture, set the size, press Make relief', picture_and_size: 'Picture and size',
  // production nest options (Part 2)
  rot_none: 'none (as drawn)', rot_30: 'every 30°', rot_free: 'free (every 10°)',
  q_thorough: 'Thorough (40 s)', q_custom: 'Custom time', resolution: 'Grid detail',
  res_auto: 'auto (from quality)', res_small: 'small parts', search_s: 'Search time (s)', search_hint: '1 to {n} seconds',
  err_search_s: 'Search time must be 1 to {n} seconds.', preset: 'Preset', preset_hint: 'Fills the fields below',
  preset_custom: 'Custom', preset_letters: 'Letters / acrylic', preset_panels: 'Panels', preset_grain: 'Grain material',
  preset_density: 'Max density', sets: 'Number of sets', sets_hint: 'Every quantity × sets', err_sets: 'Sets must be 1 to 100.',
  separate_colors: 'Keep colours on separate sheets', grain_note: 'This material has grain: parts turn 0/180 only.',
  meta_grain: 'Material has grain: rotations were limited to 0/180.',
  meta_res: 'The sheet is big: grid detail {r} mm was used instead of {q} mm.',
  meta_cancel: 'Stopped early: the best layout found so far is shown.',
  meta_cancel_partial: 'Stopped before the first layout was finished: some parts are not placed.',
  meta_sets: '{n} sets nested.', meta_colors: 'Each colour has its own sheets.',
  mirror: 'Mirror', grain: 'Grain (0/180 only)', grain_hint: 'brushed, wood, directional film', grain_badge: 'grain',
  offcut_add_title: 'Add a remnant', offcut_add_size: 'Add by size', offcut_err_size: 'Type the width and height.',
  offcut_added: 'Remnant saved', offcut_drawing: 'Drawn remnant (SVG / DXF)', offcut_upload: 'Upload outline',
  offcut_drawing_hint: 'Largest closed shape = remnant; holes in it = missing pieces', offcut_err_file: 'Choose an SVG or DXF file.',
  offcut_drawn: 'drawn', offcut_holes_n: '{n} missing piece(s)', height_mm_plain: 'Height (mm)',
};

const th = {
  nav_jobs: 'งาน', nav_relief: 'งานนูน 3D', nav_offcuts: 'เศษแผ่น', nav_settings: 'ตั้งค่า', language: 'ภาษา',
  loading: 'กำลังโหลด…', working: 'กำลังทำงาน…', ok: 'ตกลง', cancel: 'ยกเลิก', close: 'ปิด', save: 'บันทึก', saved: 'บันทึกแล้ว',
  edit: 'แก้ไข', delete: 'ลบ', deleted: 'ลบแล้ว', update: 'อัปเดต', none_yet: 'ยังไม่มีข้อมูล',
  new_job: 'งานใหม่', create_job: 'สร้างงาน', job_name: 'ชื่องาน', job_name_ph: 'เช่น ตัวอักษรหน้าร้าน',
  material: 'วัสดุ', machine: 'เครื่อง', status: 'สถานะ', sheets: 'จำนวนแผ่น', utilisation: 'การใช้พื้นที่', created: 'สร้างเมื่อ',
  no_jobs: 'ยังไม่มีงาน กด “งานใหม่” เพื่อเริ่ม', err_job_name: 'กรุณาพิมพ์ชื่องาน', back_jobs: 'กลับไปหน้างาน',
  status_draft: 'ร่าง', status_nested: 'วางแล้ว', status_exported: 'ส่งออกแล้ว', status_cut: 'ตัดแล้ว',
  delete_job: 'ลบงาน', confirm_delete_job: 'ลบงาน “{name}” และไฟล์ทั้งหมดใช่ไหม',
  parts: 'ชิ้นงาน', choose_files: 'เลือกไฟล์', drop_files: 'หรือลากไฟล์ SVG / DXF มาวางที่นี่', file_types: 'SVG หรือ DXF ไม่เกินไฟล์ละ 20 MB',
  uploading: 'กำลังอัปโหลด…', imported_n: 'นำเข้า {n} ชิ้นแล้ว', import_warnings: 'หมายเหตุการนำเข้า', no_parts: 'ยังไม่มีชิ้นงาน เพิ่มไฟล์ SVG หรือ DXF',
  name: 'ชื่อ', size_mm: 'ขนาด (มม.)', holes: 'รู', qty: 'จำนวน', marks_n: 'เส้นมาร์ค {n} เส้น ({layer})', err_qty: 'จำนวนต้องอยู่ระหว่าง 0 ถึง 999',
  nest: 'วางชิ้นงาน', nesting: 'กำลังวางชิ้นงาน…', gap_mm: 'ระยะห่างระหว่างชิ้น (มม.)', gap_hint: 'CNC: ขนาดดอกกัด + 1 มม. · เลเซอร์: 2 มม.',
  rotations: 'การหมุน', rot_0_180: '0/180', rot_90: '0/90/180/270', rot_45: 'ทุก 45°', rot_15: 'ทุก 15°',
  quality: 'คุณภาพ', q_fast: 'เร็ว', q_normal: 'ปกติ', q_fine: 'ละเอียด',
  parts_in_holes: 'วางชิ้นเล็กในรูของชิ้นใหญ่', use_offcuts: 'ใช้เศษแผ่นก่อน',
  sheet_w: 'ความกว้างแผ่น (มม.)', sheet_h: 'ความสูงแผ่น (มม.)', sheet_hint: 'เว้นว่าง = ใช้ขนาดแผ่นของวัสดุ',
  not_nested: 'ยังไม่ได้วางชิ้นงาน ตั้งค่าแล้วกด “วางชิ้นงาน”',
  sheets_used: 'แผ่นที่ใช้', unplaced: 'วางไม่ลง', cost: 'ต้นทุน', excl_vat: 'ไม่รวม VAT',
  sheets_cost: 'ค่าแผ่น', machine_cost: 'ค่าเครื่อง', set_price: 'ตั้งราคาวัสดุในหน้าตั้งค่า', cut_time: 'ตัดประมาณ {m} นาที',
  sheet: 'แผ่น', offcut: 'เศษแผ่น', leftover: 'เหลือ', parts_n: '{n} ชิ้น', click_zoom: 'คลิกเพื่อขยาย',
  unplaced_parts: 'ชิ้นงานที่วางไม่ลง:', nest_meta: 'ลองจัดวาง {a} แบบ ใช้เวลา {s} วินาที',
  export: 'ส่งออก', export_cnc: 'เครื่อง CNC: {m} ได้ไฟล์ G-code หนึ่งไฟล์ต่อแผ่นต่อดอกกัด', export_laser: 'เครื่องเลเซอร์: {m} ได้ไฟล์ DXF หนึ่งไฟล์ต่อแผ่น แยกเลเยอร์ CUT / SCORE / ENGRAVE',
  cut_tool: 'ดอกตัด', mark_tool: 'ดอกมาร์ค', same_as_cut: 'ใช้ดอกเดียวกับดอกตัด', cut_through: 'ตัดลึกลงแผ่นรอง (มม.)',
  tabs: 'เว้นสะพาน (แท็บ)', tab_width: 'ความกว้างแท็บ (มม.)', tab_height: 'ความสูงแท็บ (มม.)', climb: 'กัดแบบ Climb',
  make_gcode: 'สร้าง G-code', make_dxf: 'สร้าง DXF', making: 'กำลังสร้างไฟล์…', kerf_mm: 'ความกว้างรอยตัด (มม.)', sheet_outline: 'ใส่เส้นขอบแผ่น',
  files: 'ไฟล์', no_files: 'ยังไม่มีไฟล์', download_zip: 'ดาวน์โหลดทั้งหมด (zip)', files_ready: 'ไฟล์พร้อมแล้ว {n} ไฟล์',
  mark_cut: 'บันทึกว่าตัดแล้ว', already_cut: 'ตัดแล้ว', marked_cut: 'บันทึกว่าตัดแล้ว และเก็บเศษแผ่นแล้ว',
  confirm_cut: 'บันทึกว่างานนี้ตัดแล้วใช่ไหม เศษที่เหลือจะเข้าคลังเศษแผ่น และเศษแผ่นที่ใช้ไปจะถูกนำออก',
  minutes_n: '{n} นาที',
  relief_intro: 'เปลี่ยนภาพแบน (โลโก้ งานป้าย) เป็นงานแกะนูน 3D พร้อม G-code กัดหยาบและกัดละเอียด',
  picture: 'รูปภาพ', picture_hint: 'PNG หรือ JPG สีเรียบชัดเจน', width_mm: 'ความกว้าง (มม.)', material_mm: 'ความหนาวัสดุ (มม.)',
  max_height_mm: 'ความสูงนูนสูงสุด (มม.)', rough_bit: 'ดอกกัดหยาบ', finish_bit: 'ดอกกัดละเอียด', no_rough: 'ไม่กัดหยาบ',
  kind_flat: 'ปากตรง', kind_ball: 'หัวบอล', kind_vbit: 'ดอกวี', kind_cnc: 'เครื่อง CNC', kind_laser: 'เลเซอร์',
  make_relief: 'สร้างงานนูน', making_relief: 'กำลังสร้างงานนูน…', err_pick_image: 'กรุณาเลือกรูปภาพก่อน',
  err_relief_sizes: 'กรุณากรอกความกว้างและความหนาวัสดุ', relief_updated: 'อัปเดตงานนูนแล้ว',
  size: 'ขนาด', depth: 'ความลึก', lost_detail: 'รายละเอียดที่ดอกกัดเข้าไม่ถึง', total_time: 'เวลารวม', preview: 'ภาพตัวอย่างงานนูน',
  regions: 'พื้นที่ตามสี', colour: 'สี', area_cm2: 'พื้นที่ (ตร.ซม.)', profile: 'รูปทรง', height_mm: 'ความสูง (มม.)', round_mm: 'ความโค้ง (มม.)',
  prof_dome: 'โค้งนูน', prof_flat: 'ราบ', prof_bevel: 'ลบมุม', prof_engrave: 'แกะลึก', prof_background: 'พื้นหลัง', images: 'รูปภาพ',
  offcuts_intro: 'เศษแผ่นที่เก็บไว้เมื่อบันทึกว่างานตัดแล้ว ระบบจะใช้ก่อนเมื่อเลือก “ใช้เศษแผ่นก่อน”',
  all_materials: 'วัสดุทั้งหมด', show_used: 'แสดงที่ใช้แล้ว', no_offcuts: 'ยังไม่มีเศษแผ่น', from_job: 'จากงาน', job_n: 'งาน {n}',
  used: 'ใช้แล้ว', free: 'ว่าง', confirm_delete_offcut: 'ลบเศษแผ่นนี้ออกจากคลังใช่ไหม',
  machines: 'เครื่องจักร', tools: 'ดอกกัด', materials: 'วัสดุ', add_machine: 'เพิ่มเครื่อง', add_tool: 'เพิ่มดอกกัด', add_material: 'เพิ่มวัสดุ',
  kind: 'ประเภท', bed_w: 'ความกว้างโต๊ะ (มม.)', bed_h: 'ความยาวโต๊ะ (มม.)', bed_mm: 'โต๊ะ (มม.)', rate_per_hour: 'ค่าเครื่อง (บาท/ชม. ไม่รวม VAT)', notes: 'หมายเหตุ',
  diameter_mm: 'เส้นผ่านศูนย์กลาง (มม.)', angle_deg: 'มุมดอกวี (°)', pass_depth_mm: 'ความลึกต่อรอบ (มม.)', feed_mm_min: 'ความเร็วเดิน (มม./นาที)',
  plunge_mm_min: 'ความเร็วกดลง (มม./นาที)', rpm: 'รอบสปินเดิล (rpm)', thickness_mm: 'ความหนา (มม.)', sheet_mm: 'แผ่น (มม.)', margin_mm: 'ระยะขอบ (มม.)',
  price_per_sheet: 'ราคาต่อแผ่น (บาท ไม่รวม VAT)', price_hint: '0 = ยังไม่ได้ตั้งราคา', set_price_short: 'ตั้งราคา',
  edit_x: 'แก้ไข {name}', confirm_delete: 'ลบ “{name}” ใช่ไหม', err_required: 'กรุณากรอก{f}', err_number: '{f}ต้องเป็นตัวเลข',
  // workspace (makeover)
  theme_light: 'พื้นที่ทำงานสีสว่าง', theme_dark: 'พื้นที่ทำงานสีเข้ม', click_show: 'แสดงแผ่นนี้',
  parts_board: 'ชิ้นงานทั้งหมด · ต้องตัด {n} ชิ้น', canvas_empty_title: 'ยังไม่มีชิ้นงาน',
  canvas_empty: 'ลากไฟล์ SVG หรือ DXF มาวางที่นี่ หรือคลิกเพื่อเลือกไฟล์', copies_n: '{n} ชิ้น', cut: 'เวลาตัด',
  relief_canvas_empty_title: 'ยังไม่มีรูป', relief_canvas_empty: 'เลือกรูปที่จะแกะนูน (PNG หรือ JPG)',
  relief_regions_empty: 'กดสร้างงานนูนก่อน แล้วแต่ละสีในรูปจะมีรูปทรงของตัวเองที่นี่',
  relief_status_empty: 'เลือกรูป ตั้งขนาด แล้วกดสร้างงานนูน', picture_and_size: 'รูปและขนาด',
  // production nest options (Part 2)
  rot_none: 'ไม่หมุน (ตามแบบ)', rot_30: 'ทุก 30°', rot_free: 'อิสระ (ทุก 10°)',
  q_thorough: 'ละเอียดมาก (40 วินาที)', q_custom: 'กำหนดเวลาเอง', resolution: 'ความละเอียดตาราง',
  res_auto: 'อัตโนมัติ (ตามคุณภาพ)', res_small: 'ชิ้นเล็ก', search_s: 'เวลาค้นหา (วินาที)', search_hint: '1 ถึง {n} วินาที',
  err_search_s: 'เวลาค้นหาต้องอยู่ระหว่าง 1 ถึง {n} วินาที', preset: 'ชุดค่าสำเร็จ', preset_hint: 'กรอกค่าด้านล่างให้อัตโนมัติ',
  preset_custom: 'กำหนดเอง', preset_letters: 'ตัวอักษร / อะคริลิก', preset_panels: 'แผ่นป้าย', preset_grain: 'วัสดุมีลาย',
  preset_density: 'วางแน่นที่สุด', sets: 'จำนวนชุด', sets_hint: 'จำนวนทุกชิ้น × จำนวนชุด', err_sets: 'จำนวนชุดต้องอยู่ระหว่าง 1 ถึง 100',
  separate_colors: 'แยกแผ่นตามสี', grain_note: 'วัสดุนี้มีลาย: หมุนชิ้นงานได้แค่ 0/180',
  meta_grain: 'วัสดุมีลาย: จำกัดการหมุนไว้ที่ 0/180',
  meta_res: 'แผ่นใหญ่: ใช้ความละเอียดตาราง {r} มม. แทน {q} มม.',
  meta_cancel: 'หยุดก่อนกำหนด: แสดงแบบที่ดีที่สุดที่หาได้',
  meta_cancel_partial: 'หยุดก่อนจัดวางแบบแรกเสร็จ: บางชิ้นยังไม่ได้วาง',
  meta_sets: 'วางทั้งหมด {n} ชุด', meta_colors: 'แต่ละสีใช้แผ่นของตัวเอง',
  mirror: 'กลับด้าน', grain: 'มีลาย (หมุนได้แค่ 0/180)', grain_hint: 'แฮร์ไลน์ ไม้ ฟิล์มมีทิศทาง', grain_badge: 'มีลาย',
  offcut_add_title: 'เพิ่มเศษแผ่น', offcut_add_size: 'เพิ่มตามขนาด', offcut_err_size: 'กรุณากรอกความกว้างและความสูง',
  offcut_added: 'บันทึกเศษแผ่นแล้ว', offcut_drawing: 'เศษแผ่นจากแบบวาด (SVG / DXF)', offcut_upload: 'อัปโหลดเส้นขอบ',
  offcut_drawing_hint: 'รูปปิดที่ใหญ่ที่สุด = เศษแผ่น รูข้างในคือส่วนที่ขาดหายไป', offcut_err_file: 'กรุณาเลือกไฟล์ SVG หรือ DXF',
  offcut_drawn: 'แบบวาด', offcut_holes_n: 'ขาดหาย {n} ส่วน', height_mm_plain: 'ความสูง (มม.)',
};

const DICTS = { en, th };
let lang = (() => { try { return localStorage.getItem(KEY) || 'en'; } catch { return 'en'; } })();
if (!DICTS[lang]) lang = 'en';
const listeners = [];

export function t(key, vars = {}, fallback) {
  let s = DICTS[lang][key] ?? en[key] ?? fallback ?? key;
  for (const [k, v] of Object.entries(vars || {})) s = s.replaceAll(`{${k}}`, String(v));
  return s;
}
export const getLang = () => lang;
export function setLang(l) {
  if (!DICTS[l] || l === lang) return;
  lang = l;
  try { localStorage.setItem(KEY, l); } catch { /* private mode */ }
  listeners.forEach((fn) => fn(l));
}
export const onLangChange = (fn) => listeners.push(fn);
export const dictionaries = DICTS;
