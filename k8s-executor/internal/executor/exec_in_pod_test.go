// Package executor 容器内命令校验测试
package executor

import "testing"

func TestCheckCommand(t *testing.T) {
	cases := []struct {
		name    string
		command string
		wantErr bool
	}{
		{"空命令", "", true},
		{"只读 ps", "ps aux", false},
		{"安装工具 apt-get", "apt-get install -y curl", false},
		{"安装工具 apk", "apk add jq", false},
		{"改配置 sed -i", "sed -i s/foo/bar/ /etc/app.conf", false},
		{"管道注入", "cat a | grep b", true},
		{"重定向注入", "echo x > /etc/passwd", true},
		{"分号注入", "curl http://x ; ls", true},
		{"命令替换注入", "cat $(find / -name x)", true},
		{"连接符注入", "apt-get update && rm -rf /", true},
		{"拦截 sh 解释器", "sh -c \"rm -rf /\"", true},
		{"拦截 python 解释器", "python -c \"print(1)\"", true},
		{"拦截 node 解释器", "node -e \"process.exit()\"", true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			err := checkCommand(c.command)
			if c.wantErr && err == nil {
				t.Fatalf("期望拦截 %q，但通过了", c.command)
			}
			if !c.wantErr && err != nil {
				t.Fatalf("期望放行 %q，但被拦截: %v", c.command, err)
			}
		})
	}
}
