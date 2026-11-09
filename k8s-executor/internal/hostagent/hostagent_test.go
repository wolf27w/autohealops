package hostagent

import "testing"

func TestMatchProcesses(t *testing.T) {
	psOut := "PID %CPU %MEM RSS ARGS\n" +
		"1234 12.5 1.2 123456 /opt/app/bin/start.sh --prod\n" +
		"5678 3.1 0.8 65432 /usr/sbin/mysqld\n" +
		"9999 0.1 0.1 5000 /usr/bin/sleep 100\n"
	specs := []HostProcess{
		{Name: "java", Path: "/opt/app/bin/start.sh"},
		{Name: "mysql", Path: "mysqld"},
		{Name: "missing", Path: "/nope/not/here"},
	}
	res := matchProcesses(psOut, specs)
	if len(res) != 3 {
		t.Fatalf("期望 3 条结果，得到 %d", len(res))
	}
	if !res[0].Running || res[0].PidCount != 1 {
		t.Errorf("start.sh 应 running 且 1 个 pid，得到 %+v", res[0])
	}
	if res[0].CPU != 12.5 {
		t.Errorf("start.sh cpu 应 12.5，得到 %v", res[0].CPU)
	}
	if !res[1].Running || res[1].PidCount != 1 {
		t.Errorf("mysqld 应 running，得到 %+v", res[1])
	}
	if res[2].Running {
		t.Errorf("missing 不应 running，得到 %+v", res[2])
	}
}

func TestMatchProcessesHeaderOnly(t *testing.T) {
	res := matchProcesses("PID %CPU %MEM RSS ARGS\n", []HostProcess{{Name: "x", Path: "x"}})
	if len(res) != 1 || res[0].Running {
		t.Errorf("只有表头时不应命中，得到 %+v", res)
	}
}
