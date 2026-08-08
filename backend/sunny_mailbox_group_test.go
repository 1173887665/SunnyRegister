package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestSunnyMailboxGroupsCountAndDeleteGuard(t *testing.T) {
	s := newSunnySessionTestServer(t)
	group := SunnyMailboxGroup{Name: "测试分组"}
	if err := s.db.Create(&group).Error; err != nil {
		t.Fatalf("create group: %v", err)
	}
	mailbox := SunnyMailbox{GroupID: group.ID, Email: "group@example.com", Status: "未注册", Enabled: true}
	if err := s.db.Create(&mailbox).Error; err != nil {
		t.Fatalf("create mailbox: %v", err)
	}
	groupID := strconv.FormatUint(uint64(group.ID), 10)
	renameRec := httptest.NewRecorder()
	renameReq := httptest.NewRequest(http.MethodPut, "/api/sunny/mailbox-groups/"+groupID, strings.NewReader(`{"name":"Renamed Group"}`))
	renameReq.Header.Set("Content-Type", "application/json")
	s.sunnyMailboxGroups(renameRec, renameReq, []string{groupID})
	if renameRec.Code != http.StatusOK {
		t.Fatalf("rename status = %d, body = %s", renameRec.Code, renameRec.Body.String())
	}
	if err := s.db.First(&group, group.ID).Error; err != nil {
		t.Fatalf("reload renamed group: %v", err)
	}
	if group.Name != "Renamed Group" {
		t.Fatalf("renamed group name = %q", group.Name)
	}

	listRec := httptest.NewRecorder()
	s.sunnyMailboxGroups(listRec, httptest.NewRequest(http.MethodGet, "/api/sunny/mailbox-groups", nil), nil)
	if listRec.Code != http.StatusOK {
		t.Fatalf("list status = %d, body = %s", listRec.Code, listRec.Body.String())
	}
	var listBody struct {
		Items []struct {
			ID           uint  `json:"id"`
			MailboxCount int64 `json:"mailbox_count"`
		} `json:"items"`
	}
	if err := json.Unmarshal(listRec.Body.Bytes(), &listBody); err != nil {
		t.Fatalf("decode list: %v", err)
	}
	foundCount := int64(-1)
	for _, item := range listBody.Items {
		if item.ID == group.ID {
			foundCount = item.MailboxCount
		}
	}
	if foundCount != 1 {
		t.Fatalf("mailbox_count = %d, want 1", foundCount)
	}

	deleteRec := httptest.NewRecorder()
	s.sunnyMailboxGroups(deleteRec, httptest.NewRequest(http.MethodDelete, "/api/sunny/mailbox-groups/"+groupID, nil), []string{groupID})
	if deleteRec.Code != http.StatusConflict || !strings.Contains(deleteRec.Body.String(), "mailbox_group_not_empty") {
		t.Fatalf("non-empty delete status = %d, body = %s", deleteRec.Code, deleteRec.Body.String())
	}
	if err := s.db.Delete(&mailbox).Error; err != nil {
		t.Fatalf("delete mailbox: %v", err)
	}
	deleteRec = httptest.NewRecorder()
	s.sunnyMailboxGroups(deleteRec, httptest.NewRequest(http.MethodDelete, "/api/sunny/mailbox-groups/"+groupID, nil), []string{groupID})
	if deleteRec.Code != http.StatusOK {
		t.Fatalf("empty delete status = %d, body = %s", deleteRec.Code, deleteRec.Body.String())
	}
}

func TestSunnyMailboxGroupsSortDefaultFirstThenNewest(t *testing.T) {
	s := newSunnySessionTestServer(t)
	defaultID := s.sunnyEnsureDefaultGroup()
	base := time.Now().Add(-time.Hour)
	oldGroup := SunnyMailboxGroup{Name: "较早分组", CreatedAt: base}
	newGroup := SunnyMailboxGroup{Name: "最新分组", CreatedAt: base.Add(time.Minute)}
	if err := s.db.Create(&oldGroup).Error; err != nil {
		t.Fatalf("create old group: %v", err)
	}
	if err := s.db.Create(&newGroup).Error; err != nil {
		t.Fatalf("create new group: %v", err)
	}

	recorder := httptest.NewRecorder()
	s.sunnyMailboxGroups(recorder, httptest.NewRequest(http.MethodGet, "/api/sunny/mailbox-groups", nil), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("list status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	var payload struct {
		Items []struct {
			ID   uint   `json:"id"`
			Name string `json:"name"`
		} `json:"items"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode groups: %v", err)
	}
	if len(payload.Items) < 3 {
		t.Fatalf("group count = %d, want at least 3", len(payload.Items))
	}
	if payload.Items[0].ID != defaultID || payload.Items[0].Name != defaultGroupName {
		t.Fatalf("first group = %#v, want default group %d", payload.Items[0], defaultID)
	}
	if payload.Items[1].ID != newGroup.ID || payload.Items[2].ID != oldGroup.ID {
		t.Fatalf("non-default order = %d, %d; want %d, %d", payload.Items[1].ID, payload.Items[2].ID, newGroup.ID, oldGroup.ID)
	}
}
