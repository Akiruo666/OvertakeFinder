-- Overtake Focus: switches the followed car during a replay, following the camera plan
-- written by AC Overtake Finder next to the new replay (<replay name>.focus.json).
--
-- Which plan is used:
--  1. The plan named after the replay that is playing ([REPLAY] FILENAME in
--     Documents\Assetto Corsa\cfg\race.ini), in the replay folder, also when Content
--     Manager renamed it to "<name>.focus.json.acreplay".
--  2. Otherwise the plan whose car positions match what is on screen: every plan holds
--     where each car is at every second of its replay.
--  3. "Open plan..." picks a plan by hand.
--
-- This app was written without being able to run the game. If something does not
-- work, send a screenshot of this window (open "Details"): it shows what it found.

local sim = ac.getSim()
local REPLAY_DIR = ac.getFolder(ac.FolderID.Replays)
local RACE_INI = ac.getFolder(ac.FolderID.Cfg) .. '/race.ini'
local MATCH_M = 8            -- a car "matches" when it is this close to where the plan has it
local MATCH_SHARE = 0.8      -- share of the compared cars that must match

local plan = nil
local planFile = nil          -- path of the loaded plan
local planManual = false      -- chosen by hand: not replaced automatically
local message = 'Waiting for a replay.'
local enabled = true
local lastWanted = nil
local wasReplay = false
local lastCheck = -100
local misses = 0              -- checks in a row where the loaded plan did not match
local cache = {}              -- path -> { size = text length, data = plan or false }
local lastScan = {}           -- { file, score } of the last search, for Details
local replayName = nil        -- [REPLAY] FILENAME in race.ini
local foundBy = ''            -- 'name' or 'positions'

-- Read a field of the sim state without failing when this CSP version lacks it.
local function field(name)
  local ok, value = pcall(function() return sim[name] end)
  if ok then return value end
  return nil
end

local function fileName(path)
  return path:match('([^/\\]+)$') or path
end

-- Time in the replay in seconds, or nil when this CSP version does not tell.
local function replayTime()
  local frame, ms = field('replayCurrentFrame'), field('replayFrameMs')
  if type(frame) == 'number' and type(ms) == 'number' and ms > 0 then
    return frame * ms / 1000
  end
  local t = field('replayCurrentTime')
  if type(t) == 'number' then return t end
  return nil
end

local function parsePlan(text)
  if not text or text == '' then return nil end
  local ok, data = pcall(JSON.parse, text)
  if not ok or type(data) ~= 'table' or type(data.switches) ~= 'table' or #data.switches == 0 then return nil end
  return data
end

-- Plan from a file, read again only when the file changed size.
local function loadCached(path)
  local text = io.load(path)
  if not text then return nil end
  local c = cache[path]
  if c and c.size == #text then return c.data or nil end
  local data = parsePlan(text)
  cache[path] = { size = #text, data = data or false }
  return data
end

local function carPos(i)
  local ok, car = pcall(ac.getCar, i)
  if not ok or not car then return nil end
  local p = car.position
  if not p then return nil end
  return p.x, p.z
end

-- How well a plan fits what is on screen now: share of cars close to where the plan
-- has them at time t (positions between two samples are interpolated). nil = cannot tell.
local function score(data, t)
  local samples, step = data.samples, tonumber(data.sample_step_s) or 1
  if type(samples) ~= 'table' or #samples == 0 then return nil end
  if t < 0 or t > (tonumber(data.duration_s) or 0) + 1 then return 0 end
  local k = math.floor(t / step)
  local a = samples[math.min(#samples, k + 1)]
  local b = samples[math.min(#samples, k + 2)]
  local w = t / step - k
  local n, good = 0, 0
  local count = type(data.cars) == 'table' and #data.cars or 0     -- (#a is unreliable with nulls)
  local live = field('carsCount')
  if type(live) == 'number' then count = math.min(count, live) end
  for i = 1, count do
    local pa, pb = a and a[i], b and b[i]
    if type(pa) == 'table' and type(pb) == 'table' then
      local x, z = carPos(i - 1)
      if x then
        local px = pa[1] + (pb[1] - pa[1]) * w
        local pz = pa[2] + (pb[2] - pa[2]) * w
        n = n + 1
        if (x - px) ^ 2 + (z - pz) ^ 2 <= MATCH_M ^ 2 then good = good + 1 end
      end
    end
  end
  if n == 0 then return nil end
  return good / n
end

local function usePlan(path, data, manual)
  if manual then foundBy = 'hand' end
  plan, planFile, planManual, lastWanted, misses = data, path, manual, nil, 0
  message = string.format('Plan "%s": %d switches, %.0f s.', fileName(path), #data.switches,
    tonumber(data.duration_s) or 0)
end

local function planFiles()
  local out = {}
  local ok, list = pcall(io.scanDir, REPLAY_DIR, '*.json*')
  if ok and type(list) == 'table' then
    for _, name in ipairs(list) do
      if name:match('%.json$') or name:match('%.json%.acreplay$') then
        out[#out + 1] = REPLAY_DIR .. '/' .. name
      end
    end
  end
  return out
end

-- Name of the replay that is playing: [REPLAY] FILENAME in cfg\race.ini (when ACTIVE=1).
local function currentReplayName()
  local ok, ini = pcall(ac.INIConfig.load, RACE_INI)
  if not ok or not ini then return nil end
  local function get(key)
    local v = ini:get('REPLAY', key, '')
    if type(v) == 'table' then v = v[1] end
    return v ~= nil and tostring(v) or ''
  end
  local name = get('FILENAME')
  if name == '' or get('ACTIVE') == '0' then return nil end
  return name
end

-- Plan files named after the replay (also the names Content Manager gives them).
local function namedCandidates(name)
  local stem = fileName(name):gsub('%.acreplay$', '')
  local out = {}
  for _, suffix in ipairs({ '.focus.json', '.focus.json.acreplay', '.json', '.json.acreplay' }) do
    out[#out + 1] = REPLAY_DIR .. '/' .. stem .. suffix
  end
  return out
end

-- 1. the plan named after the replay in race.ini; 2. otherwise the plan whose car
-- positions match what is on screen.
local function searchPlan()
  replayName = currentReplayName()
  if replayName then
    for _, path in ipairs(namedCandidates(replayName)) do
      local data = loadCached(path)
      if data then
        usePlan(path, data, false)
        foundBy = 'name'
        return
      end
    end
  end
  local t = replayTime()
  if t == nil then
    message = 'No plan named after the replay, and this CSP version does not tell the replay time.\nUse "Open plan...".'
    return
  end
  lastScan = {}
  local best, bestPath, bestScore = nil, nil, 0
  for _, path in ipairs(planFiles()) do
    local data = loadCached(path)
    if data then
      local s = score(data, t)
      lastScan[#lastScan + 1] = { fileName(path), s }
      if s and s > bestScore then best, bestPath, bestScore = data, path, s end
    end
  end
  if best and bestScore >= MATCH_SHARE then
    usePlan(bestPath, best, false)
    foundBy = 'positions'
  elseif not planManual then
    plan, planFile = nil, nil
    message = 'No plan in the replay folder matches this replay (yet).\n'
      .. 'It is checked again while the replay plays, or use "Open plan...".'
  end
end

local function openDialog()
  os.openFileDialog({
    title = 'Open camera plan',
    folder = REPLAY_DIR,
    fileTypes = {
      { name = 'Camera plans', mask = '*.focus.json;*.json;*.focus.json.acreplay' },
    },
    addAllFilesFileType = true,
  }, function(err, filename)
    if err then
      message = 'Could not open the file dialog: ' .. tostring(err)
    elseif filename and filename ~= '' then
      local data = parsePlan(io.load(filename))
      if data then usePlan(filename, data, true)
      else message = 'This is not a camera plan from AC Overtake Finder:\n' .. filename end
    end
  end)
end

local function wantedCar(t)
  local want = plan.switches[1].car
  for i = 1, #plan.switches do
    if plan.switches[i].t <= t + 0.02 then want = plan.switches[i].car else break end
  end
  return want
end

function script.update(dt)
  local replay = field('isReplayActive') and true or false
  if replay and not wasReplay then
    planManual = false          -- a replay started: find its own plan
    lastCheck = -100
  end
  wasReplay = replay
  if not replay then return end

  -- every 2 s: find a plan, or check that the loaded one still fits (another replay?)
  if os.clock() - lastCheck > 2 then
    lastCheck = os.clock()
    if plan == nil then
      searchPlan()
    elseif not planManual and foundBy == 'name' then
      if currentReplayName() ~= replayName then plan = nil; searchPlan() end
    elseif not planManual then
      local t = replayTime()
      local s = t and score(plan, t)
      if s ~= nil and s < 0.5 then misses = misses + 1 else misses = 0 end
      if misses >= 2 then plan = nil; searchPlan() end
    end
  end

  if not enabled or plan == nil then return end
  local t = replayTime()
  if t == nil then return end
  local want = wantedCar(t)
  if want ~= lastWanted then
    lastWanted = want
    if ac.focusCar then ac.focusCar(want) end
  end
end

local function describe(value)
  if value == nil then return 'not found' end
  return tostring(value)
end

function script.windowMain(dt)
  ui.text(message)
  if ui.checkbox('Follow the plan', enabled) then enabled = not enabled end
  ui.sameLine()
  if ui.button('Open plan...') then openDialog() end
  ui.sameLine()
  if ui.button('Search again') then planManual = false; plan = nil; searchPlan() end
  ui.separator()
  ui.text('Replay (race.ini): ' .. describe(replayName))
  if planFile then
    ui.text('Plan file: ' .. fileName(planFile) .. (planManual and '  (chosen by hand)'
      or foundBy == 'name' and '  (same name as the replay)' or '  (recognised by car positions)'))
  end
  local focused = field('focusedCar')
  local name = ''
  if type(focused) == 'number' and plan and plan.cars then name = plan.cars[focused + 1] or '' end
  ui.text('Replay active: ' .. describe(field('isReplayActive')))
  ui.text('Replay time: ' .. describe(replayTime()))
  ui.text('Followed car: ' .. describe(focused) .. '  ' .. name)
  if plan and lastWanted ~= nil then
    ui.text('Plan wants car: ' .. tostring(lastWanted) .. '  ' .. tostring(plan.cars and plan.cars[lastWanted + 1]))
  end
  if plan and foundBy ~= 'positions' and replayTime() then
    local s = score(plan, replayTime())
    if s ~= nil and s < MATCH_SHARE then
      ui.textColored(string.format('The cars are not where this plan has them (%.0f%% match): wrong replay?', s * 100),
        rgbm(1, 0.6, 0.2, 1))
    end
  end
  ui.treeNode('Details', function ()
    ui.text('Replay folder: ' .. REPLAY_DIR)
    ui.text('Plans compared (share of cars where the plan has them):')
    if #lastScan == 0 then ui.text('  (none)') end
    for _, row in ipairs(lastScan) do
      ui.text(string.format('  %s: %s', row[1], row[2] and string.format('%.0f%%', row[2] * 100) or 'no positions (old plan)'))
    end
    ui.text('Found in this CSP version:')
    ui.text('  ac.focusCar: ' .. (ac.focusCar and 'yes' or 'NO'))
    for _, n in ipairs({ 'carsCount', 'replayCurrentFrame', 'replayFrames', 'replayFrameMs', 'replayCurrentTime' }) do
      ui.text('  sim.' .. n .. ': ' .. describe(field(n)))
    end
  end)
end
