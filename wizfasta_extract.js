/*
 * Wizfasta 판매상품 추출 스니펫
 * ------------------------------------------------------------
 * 사용법:
 *   1) Wizfasta 로그인 후 [상품관리 > 판매상품등록] 이동
 *   2) 쇼핑몰 선택=스토어팜, 등록유형=일반상품 선택 후 [조회]
 *   3) 브라우저 개발자도구(F12) > Console 에 아래 전체를 붙여넣고 실행
 *   4) wizfasta_products.json 파일이 다운로드됨 → 프로그램의 data/ 폴더에 넣기
 *
 * 동작: 그리드 내부 데이터(window.whus_data.grid)에서 필요한 필드만 추출하여
 *       품목코드(Mpm_Pr_Cd) 기준 JSON 으로 저장한다.
 */
(function () {
  if (!window.whus_data || !Array.isArray(window.whus_data.grid)) {
    alert("그리드 데이터를 찾을 수 없습니다. 먼저 [조회]를 실행했는지 확인하세요.");
    return;
  }
  var gd = window.whus_data.grid;
  function midcat(r) {
    var cands = ['Ppm_MCls_Nm', 'Ppm_M_Cls_Nm', 'Ppm_Mid_Cls_Nm', 'Ppm_MCate_Nm', '중분류'];
    for (var i = 0; i < cands.length; i++) { if (r[cands[i]] != null && r[cands[i]] !== '') return r[cands[i]]; }
    for (var k in r) { if (/(중분류)|(m_?cls.*nm)|(mid.*cls)|(m_?cate.*nm)/i.test(k) && r[k] != null && r[k] !== '') return r[k]; }
    return '';
  }
  var rows = gd.map(function (r) {
    return {
      품목코드: String(r.Mpm_Pr_Cd),
      브랜드: r.Ppm_Bnd_Nm,
      중분류: midcat(r),
      모델명: r.Ppm_Mdl_Nm,
      판매상품명: r.Mpm_Mk_Pr_Nm,
      시즌: r.Ppm_Year_Ss_Cd,
      원가: r.Ppm_Cost,
      기준판매가: r.Ppm_Std_Sell_Amt,
      공급가: r.Mpm_Suply_Price,
      판매가: r.Mpm_Sell_Price,
      수수료율: r.Mpm_Mk_Rate
    };
  });
  var payload = {
    shop: "스토어팜",
    type: "일반상품",
    count: rows.length,
    rows: rows
  };
  var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url;
  a.download = "wizfasta_products.json";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  console.log("추출 완료: " + rows.length + "건 → wizfasta_products.json 다운로드됨");
})();
