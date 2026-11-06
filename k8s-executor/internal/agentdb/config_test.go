package agentdb

import "testing"

func TestLoadConfigsFromInlineJSON(t *testing.T) {
	cases := []struct {
		name    string
		raw     string
		want    int
		wantErr bool
	}{
		{"数组", `[{"key":"biz-mysql","host":"mysql.default.svc"},{"key":"biz-pg","type":"postgres","host":"pg.default.svc"}]`, 2, false},
		{"dbs 包裹", `{"dbs":[{"key":"a","host":"h"}]}`, 1, false},
		{"缺 key", `[{"host":"h"}]`, 0, true},
		{"key 重复", `[{"key":"a","host":"h"},{"key":"a","host":"h2"}]`, 0, true},
		{"类型不支持", `[{"key":"a","type":"oracle","host":"h"}]`, 0, true},
		{"缺 host", `[{"key":"a"}]`, 0, true},
		{"非法 JSON", `not-json`, 0, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Setenv("AGENT_DB_CONFIG", c.raw)
			got, err := LoadConfigs("")
			if c.wantErr {
				if err == nil {
					t.Fatalf("期望报错，实际成功: %+v", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("不期望报错: %v", err)
			}
			if len(got) != c.want {
				t.Fatalf("数量不符: got %d want %d", len(got), c.want)
			}
		})
	}
}

func TestConfigNormalize(t *testing.T) {
	t.Setenv("AGENT_DB_CONFIG", `[{"key":" biz-mysql ","host":" mysql.default.svc "}]`)
	cfgs, err := LoadConfigs("")
	if err != nil || len(cfgs) != 1 {
		t.Fatalf("load 失败: %v %+v", err, cfgs)
	}
	c := cfgs[0]
	if c.Key != "biz-mysql" || c.Host != "mysql.default.svc" {
		t.Fatalf("去空格失败: %+v", c)
	}
	if c.Type != "mysql" || c.Port != 3306 {
		t.Fatalf("默认值不对: %+v", c)
	}
	if c.DisplayName() != "biz-mysql" {
		t.Fatalf("name 缺省应等于 key: %q", c.DisplayName())
	}
}
