-- Overtake Focus: switches the followed car during a replay, following the plan
-- written by AC Overtake Finder (Documents\Assetto Corsa\overtake_focus\active.json).
--
-- This app was written without being able to run the game. Names of some replay
-- fields differ between CSP versions, so the window shows what it found; if
-- something does not work, send a screenshot of this window.

local sim = ac.getSim()
local FILE = ac.getFolder(ac.FolderID.Documents) .. '/Assetto Corsa/overtake_focus/active.json'

local plan = nil
local message = 'Plan not loaded yet.'
local enabled = true
local lastWanted = nil
local lastLoadTime = -1000

-- Read a field of the sim state without failing when this CSP version lacks it.
local function field(name)
  local ok, value = pcall(function() return sim[name] end)
  if ok then return value end
  return nil
end

local function loadPlan()
  lastLoadTime = os.clock()
  local text = io.load(FILE)
  if not text or text == '' then
    plan, message = nil, 'No plan found at:\n' .. FILE
    return
  end
  local ok, data = pcall(JSON.parse, text)
  if not ok or type(data) ~= 'table' or type(data.switches) ~= 'table' or #data.switches == 0 then
    plan, message = nil, 'The plan file could not be read.'
    return
  end
  plan = data
  lastWanted = nil
  message = string.format('Plan for "%s": %d switches, %.0f s.', tostring(data.replay), #data.switches,
    tonumber(data.duration_s) or 0)
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
  if plan == nil and os.clock() - lastLoadTime > 3 then loadPlan() end
  if not enabled or plan == nil or not field('isReplayActive') then return end
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
  if ui.button('Reload plan') then loadPlan() end
  ui.separator()
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
      ui.textColored('This replay (' .. string.format('%.0f', length) .. ' s) is not the one the plan was made for.',
        rgbm(1, 0.6, 0.2, 1))
    end
  end
  ui.separator()
  ui.text('Found in this CSP version:')
  ui.text('  ac.focusCar: ' .. (ac.focusCar and 'yes' or 'NO'))
  for _, n in ipairs({ 'replayCurrentFrame', 'replayFrames', 'replayFrameMs', 'replayPlaybackRate', 'replayCurrentTime' }) do
    ui.text('  sim.' .. n .. ': ' .. describe(field(n)))
  end
end
