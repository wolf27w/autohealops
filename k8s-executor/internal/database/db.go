// Package database 提供 GORM 连接（MySQL，不执行 AutoMigrate）
package database

import (
	"fmt"
	"log"

	"gorm.io/driver/mysql"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

// DB 全局 GORM 实例
var DB *gorm.DB

// Init 初始化 MySQL 连接
// 注意：不执行 AutoMigrate（表结构由 api-server 的 alembic 统一管理），只做连接校验
func Init(dsn string) error {
	db, err := gorm.Open(mysql.Open(dsn), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Warn),
	})
	if err != nil {
		return fmt.Errorf("连接 MySQL 失败: %w", err)
	}
	// 连接校验（ping）
	sqlDB, err := db.DB()
	if err != nil {
		return fmt.Errorf("获取底层连接失败: %w", err)
	}
	if err := sqlDB.Ping(); err != nil {
		return fmt.Errorf("MySQL ping 失败: %w", err)
	}
	// 连接池配置
	sqlDB.SetMaxOpenConns(50)
	sqlDB.SetMaxIdleConns(10)
	DB = db
	log.Println("[database] MySQL 连接成功（表结构由 api-server alembic 管理，本服务不迁移）")
	return nil
}

// GetDB 返回全局 GORM 实例
func GetDB() *gorm.DB {
	return DB
}
