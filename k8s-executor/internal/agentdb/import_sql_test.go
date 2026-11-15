package agentdb

import (
	"reflect"
	"testing"
)

func TestSplitSQL(t *testing.T) {
	cases := []struct {
		name string
		sql  string
		want []string
	}{
		{
			"多语句",
			"SELECT 1; SELECT 2; SELECT 3;",
			[]string{"SELECT 1", "SELECT 2", "SELECT 3"},
		},
		{
			"引号内分号",
			"INSERT INTO t VALUES ('a;b'); SELECT 2;",
			[]string{"INSERT INTO t VALUES ('a;b')", "SELECT 2"},
		},
		{
			"双引号内分号",
			`INSERT INTO t VALUES ("a;b"); SELECT 2;`,
			[]string{`INSERT INTO t VALUES ("a;b")`, "SELECT 2"},
		},
		{
			"反引号内分号",
			"SELECT `a;b` FROM t; SELECT 2;",
			[]string{"SELECT `a;b` FROM t", "SELECT 2"},
		},
		{
			"转义引号",
			`INSERT INTO t VALUES ('it\'s;ok'); SELECT 2;`,
			[]string{`INSERT INTO t VALUES ('it\'s;ok')`, "SELECT 2"},
		},
		{
			"成对单引号转义",
			"INSERT INTO t VALUES ('a'';b'); SELECT 2;",
			[]string{"INSERT INTO t VALUES ('a'';b')", "SELECT 2"},
		},
		{
			"行注释 -- 内分号",
			"SELECT 1; -- 注释;含分号\nSELECT 2;",
			[]string{"SELECT 1", "-- 注释;含分号\nSELECT 2"},
		},
		{
			"行注释 # 内分号",
			"SELECT 1; # 注释;含分号\nSELECT 2;",
			[]string{"SELECT 1", "# 注释;含分号\nSELECT 2"},
		},
		{
			"块注释内分号",
			"SELECT 1; /* 注释;含分号 */ SELECT 2;",
			[]string{"SELECT 1", "/* 注释;含分号 */ SELECT 2"},
		},
		{
			"空语句与连续分号",
			";SELECT 1;; SELECT 2;",
			[]string{"SELECT 1", "SELECT 2"},
		},
		{
			"尾部分号",
			"SELECT 1; SELECT 2;",
			[]string{"SELECT 1", "SELECT 2"},
		},
		{
			"双横线非注释（减减）",
			"SELECT 1--2; SELECT 2;",
			[]string{"SELECT 1--2", "SELECT 2"},
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := splitSQLStatements(c.sql)
			if !reflect.DeepEqual(got, c.want) {
				t.Fatalf("\ngot  %#v\nwant %#v", got, c.want)
			}
		})
	}
}

func TestIsCommentOnly(t *testing.T) {
	cases := []struct {
		s    string
		want bool
	}{
		{"-- 纯注释", true},
		{"# 纯注释", true},
		{"/* 纯块注释 */", true},
		{"\n  -- 带空行\n", true},
		{"SELECT 1", false},
		{"-- 注释\nSELECT 1", false},
	}
	for _, c := range cases {
		if got := isCommentOnly(c.s); got != c.want {
			t.Fatalf("isCommentOnly(%q)=%v want %v", c.s, got, c.want)
		}
	}
}
