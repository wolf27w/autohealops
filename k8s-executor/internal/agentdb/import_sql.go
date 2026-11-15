// Package agentdb —— 原始 SQL 导入：语句切分（状态机）+ 逐条执行（stop/skip 容错）。
package agentdb

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

const (
	maxSingleStmtBytes = 10 << 20  // 单条语句上限 10MB
	maxImportSQLBytes  = 200 << 20 // 原始 SQL 总大小上限 200MB
	stmtTimeout        = 60 * time.Second
	importOverall      = 30 * time.Minute
)

type sqlErrItem struct {
	Index         int    `json:"index"`
	StatementHead string `json:"statement_head"`
	Error         string `json:"error"`
}

// ImportSqlRawByKey 执行一段原始 SQL（多条用 ; 分隔）。payload:
//
//	{"key","db":"可选","sql":"...","mode":"stop"|"skip"}
//
// 响应：{"total","executed","skipped","failed","stopped","errors":[...]}
func ImportSqlRawByKey(ctx context.Context, payload []byte) ([]byte, error) {
	var req struct {
		Key  string `json:"key"`
		DB   string `json:"db"`
		SQL  string `json:"sql"`
		Mode string `json:"mode"` // stop（遇错停止）| skip（跳过继续）
	}
	if err := json.Unmarshal(payload, &req); err != nil {
		return nil, fmt.Errorf("解析请求失败: %w", err)
	}
	if req.Key == "" {
		return nil, fmt.Errorf("key 必填")
	}
	if strings.TrimSpace(req.SQL) == "" {
		return nil, fmt.Errorf("sql 不能为空")
	}
	mode := strings.ToLower(strings.TrimSpace(req.Mode))
	if mode == "" {
		mode = "stop"
	}
	if mode != "stop" && mode != "skip" {
		return nil, fmt.Errorf("不支持的 mode=%s（stop/skip）", req.Mode)
	}
	if len(req.SQL) > maxImportSQLBytes {
		return nil, fmt.Errorf("SQL 总大小超过 200MB，拒绝导入")
	}

	db, cfg, err := openByKey(req.Key)
	if err != nil {
		return nil, err
	}
	defer db.Close()
	if req.DB == "" {
		req.DB = cfg.Database
	}
	if req.DB != "" {
		if err := validateIdent(req.DB); err != nil {
			return nil, err
		}
	}

	overallCtx, cancel := context.WithTimeout(ctx, importOverall)
	defer cancel()

	if req.DB != "" {
		if _, err := db.ExecContext(overallCtx, "USE "+quoteIdent(req.DB)); err != nil {
			return nil, fmt.Errorf("切换数据库失败: %w", err)
		}
	}

	stmts := splitSQLStatements(req.SQL)
	// 预检：任一条超过 10MB 直接拒绝，避免边执行边才发现（不产生部分副作用）。
	for i, s := range stmts {
		if len(s) > maxSingleStmtBytes {
			return nil, fmt.Errorf("第 %d 条语句超过 10MB（%d 字节），拒绝导入", i, len(s))
		}
	}

	total := len(stmts)
	executed, skipped, failed := 0, 0, 0
	stopped := false
	errs := make([]sqlErrItem, 0)

	for i, stmt := range stmts {
		if isCommentOnly(stmt) {
			skipped++
			continue
		}
		stmtCtx, cancelStmt := context.WithTimeout(overallCtx, stmtTimeout)
		_, err := db.ExecContext(stmtCtx, stmt)
		cancelStmt()
		if err != nil {
			failed++
			errs = append(errs, sqlErrItem{Index: i, StatementHead: stmtHead(stmt), Error: err.Error()})
			if mode == "stop" {
				stopped = true
				break
			}
			continue
		}
		executed++
	}

	out, _ := json.Marshal(map[string]any{
		"total":    total,
		"executed": executed,
		"skipped":  skipped,
		"failed":   failed,
		"stopped":  stopped,
		"errors":   errs,
	})
	return out, nil
}

// splitSQLStatements 用状态机按 ; 拆分 SQL：
//   - 正确处理单引号/双引号/反引号内的分号（含 \ 转义与成对引号）
//   - 正确处理 -- 行注释、# 行注释、/* */ 块注释内的分号
//   - 丢弃空白/空语句，返回每条语句的 trim 后文本
//
// 不引入第三方依赖（纯字节状态机）。
func splitSQLStatements(sql string) []string {
	out := make([]string, 0, 16)
	var cur strings.Builder
	n := len(sql)
	i := 0
	for i < n {
		c := sql[i]
		switch {
		case c == '\'' || c == '"' || c == '`':
			quote := c
			cur.WriteByte(c)
			i++
			for i < n {
				ch := sql[i]
				if ch == '\\' && i+1 < n {
					cur.WriteByte(ch)
					cur.WriteByte(sql[i+1])
					i += 2
					continue
				}
				if ch == quote {
					if i+1 < n && sql[i+1] == quote { // 成对引号转义（'' / "" / ``）
						cur.WriteByte(ch)
						cur.WriteByte(ch)
						i += 2
						continue
					}
					cur.WriteByte(ch)
					i++
					break
				}
				cur.WriteByte(ch)
				i++
			}
		case c == '-' && i+1 < n && sql[i+1] == '-':
			// MySQL 行注释要求第二个 '-' 后跟空白/控制字符（-- 注释）
			if i+2 >= n || sql[i+2] == ' ' || sql[i+2] == '\t' || sql[i+2] == '\n' || sql[i+2] == '\r' {
				for i < n && sql[i] != '\n' {
					cur.WriteByte(sql[i])
					i++
				}
				continue
			}
			cur.WriteByte(c)
			i++
		case c == '#':
			for i < n && sql[i] != '\n' {
				cur.WriteByte(sql[i])
				i++
			}
		case c == '/' && i+1 < n && sql[i+1] == '*':
			cur.WriteByte(c)
			cur.WriteByte(sql[i+1])
			i += 2
			for i < n {
				if sql[i] == '*' && i+1 < n && sql[i+1] == '/' {
					cur.WriteByte('*')
					cur.WriteByte('/')
					i += 2
					break
				}
				cur.WriteByte(sql[i])
				i++
			}
		case c == ';':
			if s := strings.TrimSpace(cur.String()); s != "" {
				out = append(out, s)
			}
			cur.Reset()
			i++
		default:
			cur.WriteByte(c)
			i++
		}
	}
	if s := strings.TrimSpace(cur.String()); s != "" {
		out = append(out, s)
	}
	return out
}

// isCommentOnly 判断一条语句去掉注释与空白后是否为空（即「纯注释语句」，跳过不执行）。
func isCommentOnly(s string) bool {
	n := len(s)
	i := 0
	for i < n {
		c := s[i]
		switch {
		case c == ' ' || c == '\t' || c == '\n' || c == '\r':
			i++
		case c == '-' && i+1 < n && s[i+1] == '-' &&
			(i+2 >= n || s[i+2] == ' ' || s[i+2] == '\t' || s[i+2] == '\n' || s[i+2] == '\r'):
			for i < n && s[i] != '\n' {
				i++
			}
		case c == '#':
			for i < n && s[i] != '\n' {
				i++
			}
		case c == '/' && i+1 < n && s[i+1] == '*':
			i += 2
			for i+1 < n && !(s[i] == '*' && s[i+1] == '/') {
				i++
			}
			if i+1 < n {
				i += 2
			} else {
				i = n
			}
		default:
			return false
		}
	}
	return true
}

// stmtHead 取语句头部（单行、限长）用于错误展示。
func stmtHead(s string) string {
	s = strings.TrimSpace(s)
	s = strings.ReplaceAll(s, "\r", " ")
	s = strings.ReplaceAll(s, "\n", " ")
	s = strings.Join(strings.Fields(s), " ")
	if len(s) > 160 {
		return s[:160] + "..."
	}
	return s
}
