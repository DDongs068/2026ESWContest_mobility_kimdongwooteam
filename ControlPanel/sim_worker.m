clearvars; clc;

WORK_DIR   = '/Users/ddongsmac/Desktop/Campus/임소공/임소공 결선 자료/임소공_W10_noBal_ntc/aibms_normal_gen/model_work_w2';
RUN_DIR    = fullfile(fileparts(mfilename('fullpath')), 'runtime');
CACHE_DIR  = fullfile(RUN_DIR, 'sim_cache');
JOB_DIR    = fullfile(RUN_DIR, 'jobs');
READY_FILE = fullfile(RUN_DIR, 'worker.ready');
QUIT_FILE  = fullfile(RUN_DIR, 'worker.quit');
MODEL_NAME = 'AI_BMS_4S';
OUT_DT     = 0.1;
RAMP_S     = 120;
LOAD_IDLE  = 0.02;
POLL_S     = 0.2;
PLOGIC_W   = 0.546;

for d = {RUN_DIR, CACHE_DIR, JOB_DIR}
    if ~isfolder(d{1}), mkdir(d{1}); end
end
if isfile(READY_FILE), delete(READY_FILE); end
if isfile(QUIT_FILE), delete(QUIT_FILE); end
cd(WORK_DIR); addpath(WORK_DIR);
Simulink.fileGenControl('set', 'CacheFolder', CACHE_DIR, 'CodeGenFolder', CACHE_DIR, 'createDir', true);
load_system(MODEL_NAME); warning('off', 'all');
P0 = aibms_parameters();
P0.aux.Plogic = PLOGIC_W;
P0.bms.balanceEnable = false;
fid = fopen(READY_FILE, 'w'); fprintf(fid, '%s\n', datestr(now)); fclose(fid);
fprintf('[worker] 준비 완료\n');

while ~isfile(QUIT_FILE)
    jobs = dir(fullfile(JOB_DIR, '*.json'));
    if isempty(jobs), pause(POLL_S); continue; end
    f = fullfile(JOB_DIR, jobs(1).name);
    [~, id] = fileparts(f);
    done = struct('ok', false, 'error', '', 'soc0', [], 'wall_s', NaN, 'trip_s', NaN, 'trip_flags', '');
    try
        job = jsondecode(fileread(f));
        [P, S, soc0] = build_case(P0, job, RAMP_S, LOAD_IDLE);
        assignin('base', 'P', P); assignin('base', 'S', S);
        t_wall = tic; out = sim(MODEL_NAME); done.wall_s = toc(t_wall);
        [T, trip, fl] = to_table(out, job.pre_s, job.dur_s, OUT_DT);
        csv = fullfile(JOB_DIR, [id '.csv']);
        writetable(T, [csv '.part'], 'FileType', 'text'); movefile([csv '.part'], csv, 'f');
        done.ok = true; done.soc0 = soc0; done.trip_s = trip; done.trip_flags = fl;
        fprintf('[worker] %s %s: %.1f s, soc0 %s\n', id, job.scenario, done.wall_s, mat2str(soc0, 3));
    catch err
        done.error = err.message;
        fprintf('[worker] %s 실패: %s\n', id, err.message);
    end
    delete(f);
    d = fullfile(JOB_DIR, [id '.done']);
    fid = fopen([d '.part'], 'w'); fprintf(fid, '%s', jsonencode(done)); fclose(fid);
    movefile([d '.part'], d, 'f');
end
if isfile(READY_FILE), delete(READY_FILE); end
fprintf('[worker] 종료\n');

function [P, S, soc0] = build_case(P0, job, rampS, loadIdle)

    P = P0;

    ocvV = P.cell.ocvV(:, 2); ocvS = P.cell.ocvSOC;
    r0 = mean(P.cell.R0(:));
    v = job.V(:)' - job.I * r0;
    soc0 = interp1(ocvV, ocvS, v, 'linear', 'extrap');
    soc0 = min(max(soc0, 0.005), 0.995);
    P.cell.soc0 = soc0;
    if isfield(job,'bms_off') && job.bms_off, P.bms.enabled = false; end
    P.cell.Tamb = 273.15 + median(job.T); P.cell.T0 = P.cell.Tamb;
    P.power.Vbus0 = sum(interp1(ocvS, ocvV, soc0));
    S = aibms_scenario('normal');
    S.stop = job.pre_s + job.dur_s + 1; S.logic = 1; S.iscMode = 0;
    t = 0:0.5:S.stop;
    switch job.scenario
        case 'isc_drive',  mode = 'drive';  fault = 'isc';
        case 'isc_rest',   mode = 'rest';   fault = 'isc';
        case 'isc_charge', mode = 'charge'; fault = 'isc';
        case 'overcharge', mode = 'charge'; fault = 'ovchg';
        otherwise, error('알 수 없는 시나리오: %s', job.scenario);
    end
    S.adapter = double(strcmp(mode, 'charge'));
    if strcmp(mode, 'drive')
        pwm = 0.6 * (t >= 2); load = 0.1 * ones(size(t));
    else
        pwm = zeros(size(t)); load = loadIdle * ones(size(t));
    end
    S.pwmTimes = t; S.pwm = pwm; S.loadTimes = t; S.load = load;
    if strcmp(fault, 'isc')
        S.iscMode = 2; S.iscCell = job.cell; S.iscR = job.R_ohm;
        S.iscStart = job.pre_s; S.iscEnd = inf; S.rampDuration = rampS;
        if isfield(job,'isc_delay'), S.iscStart = job.pre_s + job.isc_delay; end
        if isfield(job,'ramp_s'), S.rampDuration = job.ramp_s; end
    else
        P.charger.CV = job.cv_V;
        if isfield(job,'cc_A'), P.charger.CC = job.cc_A; end
    end
end

function [T, trip, flags] = to_table(out, pre, dur, dt)

    [tb, yb] = ts_data(out.get('bms')); [tm, ym] = ts_data(out.get('monitor'));
    tg = (pre:dt:pre+dur-dt/2)';
    yb = interp1(tb, yb, tg, 'previous', 'extrap'); ym = interp1(tm, ym, tg, 'linear', 'extrap');
    names = {'COV','CUV','OCD','OT'}; fl = any(yb(:,17:20) > 0.5, 1);
    k = find(any(yb(:,17:20) > 0.5, 2), 1);
    if isempty(k), trip = NaN; flags = ''; else, trip = tg(k) - pre; flags = strjoin(names(fl), '+'); end
    T = table(round((tg - pre)*1000), yb(:,8), yb(:,9), yb(:,10), yb(:,11), ...
        yb(:,13)-273.15, yb(:,14)-273.15, yb(:,15)-273.15, yb(:,16)-273.15, -yb(:,12), ...
        ym(:,2), ym(:,3)-ym(:,4), zeros(numel(tg),1), ...
        'VariableNames', {'ms','V1','V2','V3','V4','T1','T2','T3','T4','I','STACK','PACK','LD'});
end

function [t, y] = ts_data(ts)

    t = ts.Time(:); y = squeeze(ts.Data);
    if size(y,1) ~= numel(t), y = y.'; end
    [t, iu] = unique(t, 'last'); y = y(iu,:);
end
