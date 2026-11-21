-- Overtake Focus: switches the followed car during a replay, following the camera plan
-- written by AC Overtake Finder next to the new replay (<replay name>.focus.json).
--
-- Which plan is used:
--   1. The replay that is playing is looked up in Documents\Assetto Corsa\cfg\race.ini
--      ([REPLAY] FILENAME, written when a replay is started), and the plan with the
--      same name is searched in the replay folder. Content Manager sometimes renames
--      the plan to "<name>.focus.json.acreplay"; that name is found too.
--   2. If nothing is found (or you want another plan), "Open plan..." lets you pick one.
--
-- This app was written without being able to run the game. If something does not
-- work, send a screenshot of this window: it shows what it found.

local sim = ac.getSim()
local REPLAY_DIR = ac.getFolder(ac.FolderID.Replays)
local RACE_INI = ac.getFolder(ac.FolderID.Cfg) .. '/race.ini'

local plan = nil
local planFile = nil          -- path of the loaded plan
local planManual = false      -- chosen by hand: do not replace it automatically
local message = 'No plan loaded yet.'
local enabled = true
local lastWanted = nil
local replayName = nil        -- as read from race.ini
local triedFor = ''           -- the replay name the automatic search was done for
local searched = {}           -- paths tried by the last automatic search
local wasReplay = false
local lastIniCheck = 0

-- Read a field of the sim state without failing when this CSP version lacks it.
local function field(name)
  local ok, value = pcall(function() return sim[name] end)
  if ok then return value end
  return nil
end

local function exists(path)
  local text = io.load(path)
  return text ~= nil and text ~= ''
end

-- Folder part and file part of a path ('/' and '\' both accepted).
local function splitPath(path)
  local dir, name = path:match('^(.*)[/\\]([^/\\]+)$')
  if dir then return dir, name end
  return nil, path
end

-- Name of the replay that is playing, from race.ini, or nil.
local function currentReplayName()
  local ok, ini = pcall(ac.INIConfig.load, RACE_INI)
  if not ok or not ini then return nil end
  local name = ini:get('REPLAY', 'FILENAME', '')
  if type(name) == 'table' then name = name[1] end
  if type(name) ~= 'string' or name == '' then return nil end
  return name
end

local function readPlan(path)
  local text = io.load(path)
  if not text or text == '' then return nil, 'File is empty or cannot be read:\n' .. path end
  local ok, data = pcall(JSON.parse, text)
  if not ok or type(data) ~= 'table' or type(data.switches) ~= 'table' or #data.switches == 0 then
    return nil, 'This is not a camera plan from AC Overtake Finder:\n' .. path
  end
  return data
end

local function usePlan(path, manual)
  local data, err = readPlan(path)
  if not data then
    message = err
    return false
  end
  plan, planFile, planManual, lastWanted = data, path, manual, nil
  local _, file = splitPath(path)
  message = string.format('Plan "%s": %d switches, %.0f s.', file, #data.switches, tonumber(data.duration_s) or 0)
  return true
end

-- Plan files that belong to a replay: same name, with .focus.json, and the
-- versions Content Manager renamed to .acreplay.
local function candidates(name)
  local dir, file = splitPath(name)
  local stem = file:gsub('%.acreplay$', '')
  local folders = {}
  if dir and dir:match('^%a:') then folders[#folders + 1] = dir           -- full path
  elseif dir then folders[#folders + 1] = REPLAY_DIR .. '/' .. dir end    -- sub folder
  folders[#folders + 1] = REPLAY_DIR
  folders[#folders + 1] = REPLAY_DIR .. '/temp'
  local out = {}
  for _, folder in ipairs(folders) do
    for _, suffix in ipairs({ '.focus.json', '.focus.json.acreplay', '.json', '.json.acreplay' }) do
      out[#out + 1] = folder .. '/' .. stem .. suffix
    end
  end
  return out
end

local function searchPlan()
  replayName = currentReplayName()
  triedFor = replayName or ''
  searched = {}
  if not replayName then
    if not planManual then
      plan, planFile = nil, nil
      message = 'Could not tell which replay is playing (race.ini has no replay name).\nUse "Open plan...".'
    end
    return
  end
  for _, path in ipairs(candidates(replayName)) do
    searched[#searched + 1] = path
    if exists(path) then
      if usePlan(path, false) then return end
    end
  end
  if not planManual then
    plan, planFile = nil, nil
    message = 'No plan found for "' .. replayName .. '".\nUse "Open plan..." to pick one.'
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
      usePlan(filename, true)
    end
  end)
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
    planManual = false              -- a new replay started: look for its own plan
    searchPlan()
  elseif replay and not planManual and os.clock() - lastIniCheck > 2 then
    lastIniCheck = os.clock()       -- another replay loaded? (checked every 2 s)
    if triedFor ~= (currentReplayName() or '') then searchPlan() end
  end
  wasReplay = replay
  if not enabled or plan == nil or not replay then return end
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
  if ui.button('Search again') then planManual = false; searchPlan() end
  ui.separator()
  ui.text('Replay (race.ini): ' .. describe(replayName))
  if planFile then ui.text('Plan file: ' .. planFile .. (planManual and '  (chosen by hand)' or '')) end
  local focused = field('focusedCar')
  local name = ''
  if type(focused) == 'number' and plan and plan.cars then name = plan.cars[focused + 1] or '' end
  ui.text('Replay active: ' .. describe(field('isReplayActive')))
  ui.text('Replay time: ' .. describe(replayTime()))
  ui.text('Followed car: ' .. describe(focused) .. '  ' .. name)
  if plan and lastWanted ~= nil then
    ui.text('Plan wants car: ' .. tostring(lastWanted) .. '  ' .. tostring(plan.cars and plan.cars[lastWanted + 1]))
  end
  if plan and field('replayFrames') and field('replayFrameMs') then
    local length = field('replayFrames') * field('replayFrameMs') / 1000
    if math.abs(length - (tonumber(plan.duration_s) or 0)) > 3 then
      ui.textColored('This replay (' .. string.format('%.0f', length) .. ' s) is not the one the plan was made for ('
        .. string.format('%.0f', tonumber(plan.duration_s) or 0) .. ' s).', rgbm(1, 0.6, 0.2, 1))
    end
  end
  ui.treeNode('Details', function ()
    ui.text('Looked for:')
    for _, p in ipairs(searched) do ui.text('  ' .. p) end
    ui.text('Found in this CSP version:')
    ui.text('  ac.focusCar: ' .. (ac.focusCar and 'yes' or 'NO'))
    for _, n in ipairs({ 'replayCurrentFrame', 'replayFrames', 'replayFrameMs', 'replayPlaybackRate', 'replayCurrentTime' }) do
      ui.text('  sim.' .. n .. ': ' .. describe(field(n)))
    end
  end)
end
